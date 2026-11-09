## Why the rewrite is needed

A model and a person read in fundamentally different ways, and text produced by a model tends to be shaped by the way the model reads:

| Reader | How they take in text | What this means for the writing |
|---|---|---|
| Model | Whole passage at once; every part cross-referenced with every other part; no fatigue; weights start and end of long inputs most heavily | Tolerates flat structure, heavy repetition, exhaustive coverage, and equal weight on every point |
| Human | Linearly, one sentence or two held verbatim, everything earlier compressed to gist; attention degrades with length; builds a running interpretation | Needs a narrative thread, a clear point of emphasis, chunking, signposting, and restatement of what matters |

A model can afford to state everything and let the reader cross-reference. A person cannot. So the rewrite optimizes for the human reader's constraints: limited working memory, a single pass, fading attention, and a need to know *what matters* rather than *everything that is true*.
