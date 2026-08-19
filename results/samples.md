# Raw Samples — `medium` (124M), step 20,000

Everything below is unedited. No cherry-picking within a run, no trimming of
the bad parts, no retries. Where a sample stops mid-sentence it's because it
hit the token limit.

Reproduce any of these:

```bash
python scripts/sample.py --checkpoint checkpoints/medium/step_20000.pt \
    --temperature 0.8 --top-k 50 --seed 1337 --max-new-tokens 120 \
    "The process of photosynthesis"
```

The seed is re-applied before every prompt, so a single prompt reproduces on
its own — you don't have to re-run the whole batch to get the same text.

---

## Batch 1 — ten prompts

`temperature=0.8, top_k=50, seed=1337, max_new_tokens=120`

### "The process of photosynthesis"

> The process of photosynthesis is a chemical process that uses sunlight to produce energy for chemical reactions called chemical reactions. Photosynthesis is a process that uses light to convert carbon dioxide (CO2) into water, which is then used to fuel the photosynthesis process.
>
> Types of Photosynthesis
>
> There are two main categories of photosynthesis: light and dark. Light is the energy that a particular molecule can absorb and release into the environment. The majority of photosynthesis is done by the sun. The light that is able to penetrate the environment is called ultraviolet light and is found in the visible light spectrum.
>
> Photosynthesis is a

### "In 1969, NASA"

> In 1969, NASA's STS-1 mission, the spacecraft's primary mission, was called to a 'very high resolution' image of the dwarf planet. This had an interesting but not unexpected result. The light from the spacecraft had been coming from a high altitude region, but the image of Ceres didn't look exactly like the image it is now, and the resolution was not actually very high.
>
> The image was taken by the STS-1 camera on STS-11. The STS-11 camera is the most visible object in the image, and it was

### "The water cycle begins when"

> The water cycle begins when the water table is high.
>
> Water resources play a vital role in the world's water cycle. The water cycle has been studied under the study of environmental, economic, and societal changes over the past 400 years.
>
> Today, the water cycle is a significant contributor to the overall water resources of Australia.
>
> The water cycle is the global process of transferring water from the ocean into the atmosphere. In order for the water to be available for the economy, the water needs to be replenished.
>
> We are able to see the water cycle in two main ways.
>
> - It is a

### "Children learn to read by"

> Children learn to read by taking time to sit down to a story and be silly so they do not feel bored.
>
> A fun way to boost your child's motivation to read is to tell them stories they are interested in. Tell them stories they like to read and then be sure to ask them questions about their favorite books and tell them what the story is about.
>
> At home, try to read at least two books together per day. Reading to your child early will help your child develop a love for books, reading vocabulary, comprehension abilities, and literacy.
>
> It is important to have a quiet and stimulating environment

### "To solve a quadratic equation,"

> To solve a quadratic equation, you need to use the same techniques used in the previous step. You want to obtain the equation by subtracting the sides of a quadratic equation by adding up the sides of the same quadratic equation. The following example shows how to do this:
>
> Given the original equation, have a student solve the quadratic equation with the same solution as the original equation:
>
> When the student has reached a level which is the lowest possible value, such as the quadratic equation, the answer is given:
>
> So, the student answers this quadratic equation:
>
> The student in

### "The Roman Empire fell because"

> The Roman Empire fell because the emperor Augustus, who ruled over the whole of the Roman Empire, was forced to lose power to his rival Etruscans. However, the conflict was not a problem for the Roman Empire, but for the Roman Empire.
>
> In the end, the Romans won their independence, but their defeat was not a problem for the Roman Empire.
>
> So far as I can tell, the Roman Empire is very different from the Roman Empire today.
>
> Roman Empire – The Roman Empire
>
> After many decades of conflict, the Roman Empire was split into two sections. The Roman Empire was more like a single

### "DNA is made up of"

> DNA is made up of three elements: the four nucleotides, the three chemical bases called carbon-12, nitrogen-14, and oxygen-13. The elements are arranged alphabetically, with each element's own chemical symbol for that element.
>
> DNA's chemical symbol is 2, but its chemical symbol is 3.
>
> DNA's chemical symbol is 14 because it is made up of four different chemicals: ribonucleic acid (Rna), thymine (T), nucleic acid (C) and nucleic acid (A). The three chemicals are arranged alphabetically. All three

### "A volcano erupts when"

> A volcano erupts when the earth's mantle is subjected to severe pressure. This pressure pushes it to the surface creating a 'colder' (or 'sink') of the earth's crust that forms its floor. The crust then forms a thin layer of rock known as a lava plain. A thick layer of lava, called a lava lake, forms when the magma is pushed into a volcanic arch. The top of the volcano has a dome-shaped core that is made of lava and lava rock. When the pressure from this top of the volcano warms, the lava

### "The main causes of World War I were"

> The main causes of World War I were the collapse of the Ottoman Empire, the death of the so-called "Greater Germany" and the outbreak of World War II under the leadership of Adolf Hitler. The war's political developments and strategic implications greatly impacted the international arena. A large part of the war was the defeat of Germany at the end of WWII, and the subsequent defeat and eventual liberation of the United States. In World War II, a large proportion of the war was fought between the Allies, but many were defeated. The Allies achieved almost no success at the end of World War II. However, the Japanese

### "Fractions can be tricky for students because"

> Fractions can be tricky for students because they involve some difficult mathematical concepts. But we can help students learn to apply that understanding to their everyday lives.
>
> If you teach fractions, here are some things to keep in mind:
>
> - Identify the relationship between different numbers and write the numbers as a whole number
> - Identify the relationships between different numbers
> - Know the symbols for the numbers to write down as a whole number
> - Know the order in which fractions are written
> - Identify the numerator and denominator of the given number given in the equation
> - Apply the factors and the value of the numerator to the

---

## Batch 2 — the same two prompts at three temperatures

`top_k=50, seed=42, max_new_tokens=90`

### temperature = 0.3

**"Photosynthesis is the process by which"**

> Photosynthesis is the process by which plants convert sunlight into chemical energy. It is a process that occurs in the soil, plants, and animals. The process of photosynthesis is the process that plants use to convert sunlight into chemical energy.
>
> The process of photosynthesis is the process by which plants convert sunlight into chemical energy. It is a process that occurs in the soil, plants, and animals. The process of photosynthesis is the process that plants use to convert sunlight into chemical energy

**"The Industrial Revolution began in"**

> The Industrial Revolution began in the early 19th century as the Industrial Revolution began to take hold in the United States. The Industrial Revolution was a period of rapid industrialization and industrialization in which the United States was the dominant economic power in the world. The Industrial Revolution was a period of rapid industrialization and industrialization in which the United States was the dominant economic power in the world. The Industrial Revolution was a period of rapid industrialization and industrialization in which the United States

### temperature = 0.8

**"Photosynthesis is the process by which"**

> Photosynthesis is the process by which plants take up sugars from the air, and then convert it into sugar and energy.
>
> How does photosynthesis accomplish this? A chemical process called photosynthesis is what plants use to turn sugars into sugar.
>
> The amount of sugar in the air can vary depending on temperature, but most of the increase in concentration comes from the air.
>
> How can you find out how much sugar is in air in plants?
>
> It is important to be able to

**"The Industrial Revolution began in"**

> The Industrial Revolution began in England in the 16th century with the introduction of the steam engine, making it the world's first industrial revolution. A lot of the innovations in the Industrial Revolution were also made during the Industrial Revolution, such as the invention of steam engines and electrical power. There was also a lot of manufacturing, such as shipbuilding, printing, shipping and the development of steam engines. Although there were many inventions in the Industrial Revolution, some were completely different

### temperature = 1.0

**"Photosynthesis is the process by which"**

> Photosynthesis is the process by which plants take carbohydrates and convert it into sugar. It requires sunlight to photosynthesize sugar, which is the essential part of life. Without sunlight, the plants cannot grow and multiply. In colder temperatures, photosynthesis may take place at the expense of food and shelter. This leads to higher levels of stress. So when we come across a plant or a tree at the moment, our minds are more apt to focus on that and make decisions about where

**"The Industrial Revolution began in"**

> The Industrial Revolution began in 1815 when the first steam-powered flour mill was completed in London, England.
>
> A series of important industrial revolutions occurred at the time. These revolutionary transformations became significant events in our society as we move towards the new nation.
>
> This section contains 4,234 words
>
> (approx. 11 pages at 300 words per page)

---

## Batch 3 — unconditional

No prompt at all, just the beginning-of-document token.
`temperature=0.8, top_k=50, max_new_tokens=100`

### seed = 7

> Rocks, stones, clay, and a few other small objects were used to construct the pyramid and several other pyramids have been found in the United States. The artifacts were often used to build the pyramids of the ancient Sumerians.
>
> The Stonehenge Foundation
>
> The Stonehenge Foundation is a nonprofit organization which works to promote awareness, education, and environmental stewardship of the Ancient World.
>
> The Stonehenge Foundation, in cooperation with the Stonehenge Foundation, is working

### seed = 99

> The latest news from academia, regulators
>
> research labs and other things of interest
>
> Posted: Dec 30, 2010
>
> Biologically friendly plant materials are used to treat a range of medical and biotechnology applications for biofuels, said a team of European scientists.
>
> Biomass has been used for fuel, feed and for biofuels for many years, a major step towards making biofuels from biomass.
>
> Biomass, also known as biomass feedstock from biomass, is the most
