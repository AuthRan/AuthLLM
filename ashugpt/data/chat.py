"""Multi-turn conversations: several exchanges in one training document.

`ashugpt/data/instruction.py` teaches the model to answer *one* question.
It has no notion of what was said before, because its template has no place
to put it -- one instruction, one response, end of document. A model
trained only on that has no reason to treat earlier text as something it
said, or to expect the conversation to continue after it stops.

The change is smaller than it sounds, and lives almost entirely in two
decisions:

**Every assistant turn is supervised; everything else is masked.** That is
the same rule as single-turn prompt masking (README section 10.1),
applied per turn rather than once. The model reads the user's words --
they are in `input_ids`, attention sees all of them -- and is never asked
to produce them. A conversation with three assistant turns contributes
three supervised spans to one training example.

**EOS ends a turn, not the document.** In single-turn tuning, the
`<|endoftext|>` after the response is the last token of the example, so
"stop" and "the document is over" are the same event and the model cannot
tell them apart. Here a document contains several of them, each followed
by more conversation, so the token means "my turn is finished" -- which is
what a chat model actually needs it to mean. This is the only reason a
multi-turn model stops at the end of *its* turn instead of cheerfully
writing the user's next message too.

Roles are marked with plain text (`### User:` / `### Assistant:`), not new
special tokens. A real role token would be cleaner -- one id, unambiguous,
impossible for user text to forge -- but adding one means growing the
vocabulary, which means growing the embedding matrix, which means the new
row is randomly initialized in a model whose other 50,257 rows carry 2.46B
tokens of training. The text markers cost a handful of tokens per turn and
nothing else, and they keep every existing checkpoint loadable. The
tradeoff is that a user who types "### Assistant:" is writing something
the model may read as a role boundary; `Turn.__post_init__` refuses
content containing a marker rather than letting it through silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
from torch.utils.data import Dataset

from ashugpt.data.instruction import IGNORE_INDEX

SYSTEM_MARKER = "### System:\n"
USER_MARKER = "### User:\n"
ASSISTANT_MARKER = "### Assistant:\n"

CHAT_HEADER = (
    "The following is a conversation between a user and a helpful assistant. "
    "The assistant answers the user's questions.\n\n"
)

ROLES = ("system", "user", "assistant")
_MARKERS = {"system": SYSTEM_MARKER, "user": USER_MARKER, "assistant": ASSISTANT_MARKER}


@dataclass
class Turn:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")
        for marker in _MARKERS.values():
            if marker.strip() in self.content:
                # Not paranoia about a malicious user so much as about a
                # silent one: a conversation whose content contains a role
                # marker trains the model that role boundaries appear in
                # the middle of messages, and the resulting model will
                # invent both sides of a dialogue.
                raise ValueError(f"turn content may not contain the role marker {marker.strip()!r}")


@dataclass
class Conversation:
    turns: list[Turn] = field(default_factory=list)

    def __post_init__(self) -> None:
        if any(t.role == "system" for t in self.turns[1:]):
            raise ValueError("a system turn may only be the first turn")

    def render(self, up_to: int | None = None) -> str:
        """The full conversation as one document.

        `up_to` renders only the first N turns, which is what makes a
        prefix of a conversation addressable -- the dataset uses it to
        find where each assistant turn starts.
        """
        parts = [CHAT_HEADER]
        for turn in self.turns[: up_to if up_to is not None else len(self.turns)]:
            parts.append(f"{_MARKERS[turn.role]}{turn.content.strip()}\n\n")
        return "".join(parts)

    def render_for_generation(self) -> str:
        """The conversation so far, ending where the assistant should start.

        A model prompted with a conversation that ends after the user's
        message has to guess that it is its turn; ending the prompt with
        the assistant marker tells it, in exactly the form training used.
        """
        return self.render() + ASSISTANT_MARKER

    def split_last_answer(self) -> tuple[Conversation, Turn] | None:
        """(everything before the last assistant turn, that turn).

        What an evaluation needs in order to ask the model to produce an
        answer it can be scored against: the prefix renders to a prompt with
        `render_for_generation()`, and the held-out turn is the reference.

        The *last* assistant turn rather than the first, deliberately -- it is
        the one with the most history in front of it, and a conversation
        evaluated only at its opening turn is a single-turn evaluation wearing
        a chat template.

        None when there is no assistant turn, or when the only one is the
        first turn: a prefix of nothing is not a conversation.
        """
        positions = [i for i, turn in enumerate(self.turns) if turn.role == "assistant"]
        if not positions or positions[-1] == 0:
            return None
        index = positions[-1]
        return Conversation(self.turns[:index]), self.turns[index]

    @classmethod
    def from_messages(cls, messages: list[dict]) -> Conversation:
        """Builds from `[{"role": ..., "content": ...}]`, the shape both
        UltraChat and every chat API use."""
        return cls([Turn(role=m["role"], content=m["content"]) for m in messages])


class ChatDataset(Dataset):
    """Tokenized multi-turn conversations with every assistant turn supervised.

    Each item is (input_ids, labels), both `seq_len` long. Only the tokens
    of assistant turns -- their content and the `<|endoftext|>` that ends
    each one -- carry a label; the header, the role markers, the system
    turn and every user turn are IGNORE_INDEX.

    **Long conversations are truncated at a turn boundary, not dropped and
    not cut mid-sentence.** UltraChat conversations routinely run past
    2,000 tokens against a 512-token window, so dropping whole
    conversations would discard most of the corpus; cutting mid-turn would
    end an example in the middle of an assistant's sentence with no EOS,
    teaching exactly the "stop abruptly at the window edge" behaviour that
    instruction.py drops examples to avoid. Keeping the longest prefix that
    fits and ending after a complete assistant turn preserves both
    properties: every example is a well-formed conversation, and every
    supervised span ends where the model should stop.
    """

    def __init__(self, conversations: list[Conversation], tokenizer, seq_len: int = 512) -> None:
        self.seq_len = seq_len
        self.tokenizer = tokenizer
        self.pad_id = tokenizer.pad_id

        # (token_ids, supervised_spans) where each span is a [start, end)
        # half-open range over token_ids that should carry labels.
        self.encoded: list[tuple[list[int], list[tuple[int, int]]]] = []
        self.dropped = 0
        self.truncated = 0

        for conversation in conversations:
            ids, spans, truncated = self._encode(conversation)
            if not spans:
                # No assistant turn fits: nothing to learn from.
                self.dropped += 1
                continue
            self.encoded.append((ids, spans))
            self.truncated += int(truncated)

    def _encode(self, conversation: Conversation) -> tuple[list[int], list[tuple[int, int]], bool]:
        ids = self.tokenizer.encode(CHAT_HEADER, add_bos=True)
        spans: list[tuple[int, int]] = []
        # The state to roll back to: the last point where a complete
        # assistant turn had just ended.
        committed_ids, committed_spans = list(ids), []
        truncated = False

        for turn in conversation.turns:
            marker_ids = self.tokenizer.encode(_MARKERS[turn.role])
            body = turn.content.strip()

            if turn.role == "assistant":
                content_ids = self.tokenizer.encode(body, add_eos=True)
            else:
                content_ids = self.tokenizer.encode(body)
            tail_ids = self.tokenizer.encode("\n\n")

            start = len(ids) + len(marker_ids)
            ids = ids + marker_ids + content_ids + tail_ids

            if turn.role == "assistant":
                # The supervised span covers the answer and its EOS, and
                # stops before the "\n\n" separator: that separator belongs
                # to the document's formatting, not to what the model
                # should produce after saying its piece.
                spans = spans + [(start, start + len(content_ids))]

            # +1 because the window is sliced into seq_len inputs and
            # seq_len labels, which needs seq_len + 1 tokens -- same rule
            # as InstructionDataset.
            if len(ids) > self.seq_len + 1:
                truncated = True
                return committed_ids, committed_spans, truncated

            if turn.role == "assistant":
                committed_ids, committed_spans = list(ids), list(spans)

        return committed_ids, committed_spans, truncated

    def __len__(self) -> int:
        return len(self.encoded)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        ids, spans = self.encoded[idx]

        input_ids = torch.full((self.seq_len,), self.pad_id, dtype=torch.long)
        labels = torch.full((self.seq_len,), IGNORE_INDEX, dtype=torch.long)

        body = torch.tensor(ids, dtype=torch.long)
        n_in = min(len(ids) - 1, self.seq_len)
        input_ids[:n_in] = body[:n_in]

        # labels[t] is the token that follows input_ids[t], so a span
        # [start, end) over token positions is supervised at label
        # positions [start - 1, end - 1) -- the same one-token shift as
        # everywhere else in this repo, applied per span rather than once.
        for start, end in spans:
            label_start = max(start - 1, 0)
            label_end = min(end - 1, n_in)
            if label_end > label_start:
                labels[label_start:label_end] = body[label_start + 1 : label_end + 1]

        return input_ids, labels

    @property
    def supervised_fraction(self) -> float:
        """Share of positions that carry a label. Worth watching: a chat
        corpus with long user turns can supervise very little of what it
        costs to process."""
        supervised = sum(sum(end - start for start, end in spans) for _, spans in self.encoded)
        total = len(self.encoded) * self.seq_len
        return supervised / total if total else 0.0
