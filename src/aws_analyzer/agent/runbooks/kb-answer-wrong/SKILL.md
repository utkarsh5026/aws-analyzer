---
name: kb-answer-wrong
description: A Bedrock knowledge base gives a wrong or empty answer, says it doesn't know, or cites the wrong file. Use to tell apart a file that isn't indexed, a search that misses it, and a model that ignores it.
metadata:
  toolkits: bedrock_kb
---
# A knowledge base answers wrong

A right answer needs three things: the file is indexed, the search finds its passage, and the model uses that
passage. Check them in that order, with the user's own words.

1. **Is it healthy?** Call `kb_health`. If the last sync failed, or a data source never synced, that may be the whole
   answer. Call `kb_syncs` to see why.
2. **Is the file indexed?** If the user names the file that holds the answer, call `kb_files` with `match=` set to
   its name. A file that is failed, new, changed or skipped isn't what searches see. Say its state, and give the
   sync command the findings name.
3. **Does the search find it?** Call `search_kb` with the user's question. If the file's passage isn't among the
   results, call `file_chunks` on the file with `question=` to see where its best passage ranks, and whether its
   chunks hold the text at all (scanned pages, tables, chunks too small).
4. **Does the model use it?** If the search finds the passage, call `answer_kb` once with the same question. An answer
   that doesn't cite the passage points at the model or its prompt, not the search.

In the answer, say which of the three broke and what to change: sync the data source, change the chunking or the
parser, add metadata to filter on, or change the model or prompt. Nothing here syncs or edits a knowledge base:
give the command for the user to run.
