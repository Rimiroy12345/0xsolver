# 0xsolver

A free, local CTF investigation assistant powered by LM Studio. Four agents take turns inspecting evidence, executing tools, writing scripts, and reviewing candidate flags. No paid API keys or ChatGPT subscription required.

## Start on Windows

Keep Docker Desktop and LM Studio running. In LM Studio, load `qwen3-4b` and enable **Local Model API** on port 1234.

In PowerShell, inside your repository:

```powershell
git pull --ff-only
docker compose up --build -d
```

Open http://localhost:8000. The first Docker build downloads tools and Python packages and may take several minutes. Later starts use `docker compose up -d`.

Paste a challenge, add its files and flag format, then start an investigation. The interface shows actual commands, outputs, agent handoffs, and errors. Export the case as JSON to keep its evidence. Uploads support up to 20 files and 1 GB total (1,024 MB) per case. Files stream to disk; multipart temporary storage and saved uploads can briefly require about twice that disk space. The worker still has a 2 GB RAM limit, so process large files in chunks.

Cases persist in the Docker `cases` volume. `docker compose down` preserves cases; `docker compose down -v` deletes them.

## What it can attempt

Uploaded-file challenges: encodings, basic cryptography, archive inspection, metadata, packet captures, image analysis, binary inspection, and Python-based solving. Tools include file, strings, xxd, ExifTool, tshark, 7z, unzip, objdump, readelf, GDB and GCC. Python includes SymPy, PyCryptodome, Pillow, NumPy, pwntools and Z3.

This first version has **no internet access in the analysis worker**. Live web/pwn targets and online OSINT are not supported yet. Tools such as SageMath, Ghidra, Volatility and specialist steganography tools are not included. Large memory images may exceed the upload or worker memory limits. A model can suggest incorrect approaches; a completed run does not imply a solved challenge. Validate candidate flags with the challenge platform.

## Local AI and multiple models

Choose a model for each agent under **Agent models & run limits** before starting. All requests go to the same local LM Studio server; there are no cloud fallbacks. Agents run sequentially to reduce load. Model switching relies on LM Studio's just-in-time loading and memory management; the app does not automatically unload previous models. Manually unload unused models when memory is tight.

Defaults: 3 tool rounds per agent, 700 tokens per reply, `/no_think` requested. Model prompts use bounded excerpts of tool output and a conservative history budget for an 8,192-token context. Evidence logs retain the worker output (up to its 12,000-byte cap). Agents can inspect omitted sections with targeted commands.

Large prompts and tool outputs still take time to process on a laptop CPU. You can adjust limits before a run. Use a model that supports tool calling; not every downloaded model does. Stop takes effect between calls; an in-flight model request can take up to five minutes to time out.

## Connection troubleshooting

Docker uses `http://host.docker.internal:1234/v1` to reach LM Studio on Windows. `localhost` inside a container refers to the container itself.

```powershell
docker compose logs --tail 60 app worker
```

If the interface reports a disconnected model, check LM Studio's API server and port. Some versions require a server listening/interface option that allows Docker connections. If available, enable access from Docker/local network and permit the relevant Windows firewall connection only on a trusted network. CORS is unnecessary: the Python backend calls LM Studio. Never forward the API port on your router.

To inspect connectivity from the app container:

```powershell
docker compose exec app python -c "import httpx; print(httpx.get('http://host.docker.internal:1234/v1/models',trust_env=False).text)"
```

## Execution boundary

AI-generated commands execute automatically in a separate non-root worker container. It has an internal Docker network, a read-only root filesystem, limited CPU/memory/processes, command timeouts and capped output, and no Docker socket or laptop folders mounted. The worker shares a case volume with the app. These are practical containment measures, **not a hardened malware sandbox**: commands can modify saved case data, and adversarial binaries may exploit container/runtime vulnerabilities. Use disposable environments for hostile malware. The interface binds to laptop loopback only and is intended for one local user, not public hosting.

## Development and verification

Python 3.11+ on Linux:

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use a fake model API plus real Python/bash execution to check decoding, tool calls, multi-agent handoffs, errors, timeouts, uploads, and case persistence. They do not benchmark Qwen or guarantee that the live model will solve challenges.

Project layout: `app/main.py` handles the UI/API and saved cases; `app/engine.py` orchestrates the agents and tool calls; `app/worker.py` executes commands; `app/static/` is the browser interface; `compose.yaml` defines containment and persistent storage.
