---
description: Ask a Benchwarmers collection; every seat answers, you verify, name them all and send a verdict per seat
argument-hint: "<collection> <what to ask>"
allowed-tools: mcp__plugin_benchwarmers_benchwarmers__list_collections, mcp__plugin_benchwarmers_benchwarmers__ask, mcp__plugin_benchwarmers_benchwarmers__verdict, Read, Grep, Glob
---

Ask the Benchwarmers collection named by the first word of the arguments; the rest is what to ask it.

Arguments: $ARGUMENTS

Follow the `benchwarmers` skill. In short:

1. No collection given, or one you do not recognise: call `list_collections` and pick by its description and tags, or ask the user which one when none fits. Never substitute a single model.
2. Build a prompt that stands on its own. The seats cannot read this machine's files, so put the real material in it (the code, the diff, the file contents, line numbers), with the question stated exactly. Nothing private that should not leave this machine.
3. Call `ask` with the collection and that prompt. Add `merged: true` only if the user asked for one answer.
4. Check every claim a seat makes against the real source before you repeat it or act on it. Agreement between seats is not evidence.
5. Report: name every seat that answered, by model and seat (member, explorer), including errors and empty replies, and say which claims held up and which did not.
6. Send one `verdict` per seat you judged, by its call id, with the true claims in `real` and the untrue ones in `false`.
