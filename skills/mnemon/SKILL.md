---
name: mnemon
description: Persistent memory CLI for LLM agents. Store facts, recall past knowledge, link related memories, manage lifecycle.
---

# mnemon

## Workflow

1. **Remember**: `mnemon remember "<fact>" --cat <cat> --imp <1-5> --entities "e1,e2" --source agent`
   - Diff is built-in: duplicates skipped, conflicts auto-replaced.
   - Output includes `action` (added/updated/skipped), `id`, `semantic_candidates`, `causal_candidates`.
   - **After remembering**: immediately embed the new insight using the returned `id`:
     `mnemon embed <id>` — this activates vector similarity for that memory right away.
2. **Link** (evaluate candidates from step 1 — use judgment, not mechanical rules):
   - Review `causal_candidates`: does a genuine cause-effect relationship exist? `causal_signal` is regex-based and prone to false positives — only link if the memories are truly causally related.
   - Review `semantic_candidates`: are these memories meaningfully related? High `similarity` alone is not sufficient — skip candidates that share keywords but discuss unrelated topics.
   - Syntax: `mnemon link <id> <candidate> --type <causal|semantic> --weight <0-1> [--meta '<json>']`
3. **Recall** (hybrid RAG): `mnemon recall "<query>" --limit 10`
   - When Ollama is running, recall uses **vector similarity + graph traversal + keyword matching** fused via RRF for the best results.

## Commands

```bash
# Memory
mnemon remember "<fact>" --cat <cat> --imp <1-5> --entities "e1,e2" --source agent
mnemon link <id1> <id2> --type <type> --weight <0-1> [--meta '<json>']
mnemon recall "<query>" --limit 10
mnemon search "<query>" --limit 10
mnemon forget <id>
mnemon related <id> --edge causal
mnemon gc --threshold 0.4
mnemon gc --keep <id>
mnemon status
mnemon log

# Embeddings (RAG via Ollama + nomic-embed-text)
mnemon embed <id>          # embed a single insight immediately after remember
mnemon embed --all         # backfill embeddings for all un-embedded insights
mnemon embed --status      # show coverage: total/embedded/ollama_available

# Stores
mnemon store list
mnemon store create <name>
mnemon store set <name>
mnemon store remove <name>
```

> **Note**: the mnemon binary is at `/root/go/bin/mnemon`. Use the full path in exec calls.

## Usage with nanobot

Use the `exec` tool to run mnemon commands:

```
exec(command="/root/go/bin/mnemon recall 'user preferences'")
exec(command="/root/go/bin/mnemon remember 'User prefers dark mode' --cat preference --imp 3 --source agent")
```

After remembering, embed immediately:
```
# remember returns {"id": "abc123", "action": "added", ...}
exec(command="/root/go/bin/mnemon embed abc123")
```

## Document Ingestion (RAG over URLs and files)

When the user sends a URL (or file path) to ingest, **always launch in background** to avoid the exec timeout:

```
exec(command="nohup /root/nano_env/bin/python /root/.nanobot/workspace/skills/mnemon/ingest.py 'https://example.com/paper.pdf' > /tmp/mnemon_ingest.log 2>&1 & echo PID:$!")
```

- Accepts a **URL** (downloads automatically) or a **local file path**
- Supports PDF, TXT, MD
- Chunks text (~400 words/chunk, 50-word overlap), stores each as a `fact` memory
- Automatically runs `mnemon embed --all` at the end — no manual embedding needed
- Cleans up any downloaded temp file after ingestion

Reply to the user immediately: "Ingesting in background — I'll let you know when it's ready."

To check progress or confirm it finished:
```
exec(command="tail -20 /tmp/mnemon_ingest.log")
```

To answer questions once done:
```
exec(command="/root/go/bin/mnemon recall 'user question here' --limit 10")
```

Options (append to the command):
- `--chunk-words N` — words per chunk (default: 400)
- `--overlap-words N` — overlap between chunks (default: 50)
- `--store NAME` — target a specific Mnemon store

## Guardrails

- Never run `remember` or `link` in the main conversation — always delegate to a sub-agent via `spawn`.
- Do not store secrets, passwords, or tokens.
- Categories: `preference` · `decision` · `insight` · `fact` · `context`
- Edge types: `temporal` · `semantic` · `causal` · `entity`
- Max 8,000 chars per insight.
