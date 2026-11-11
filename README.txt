LOCAL AI CONTROL HUB — QUICK GUIDE

START YOUR QWEN MODEL
1. Reopen Model Monitor from its desktop icon after an update.
2. Open Models, search for qwen3-coder-next:q4_K_M and select it.
3. Open Model settings to pick your OpenCode project folder.
4. Keep 32K–64K context. Priority is maximum measured speed; 50+ tokens/sec
   is an aspiration, 25 is the minimum. Neither number caps the comparison.
5. Save work and close unused large models. Click Optimize model.
6. Large-input tests can take minutes per GPU route. Stop test ends only the
   test's own private model server.
7. A recommendation is saved automatically only when GPU/context checks, the
   32K long-chat minimum and the measured writing-speed minimum pass. Slower,
   smaller or unmeasured results need your explicit Save anyway choice.
8. Click Start in OpenCode. It opens a terminal; VS Code is not opened.
9. Keep the hub open. Model settings > Undo optimization restores the prior setup.

WHAT OPTIMIZE DOES
The long-chat target is 65,536 tokens, with 32,768 as the minimum. It tests
supported GPU routes at both 64K and 32K, even if a route already exceeds 50
tokens/sec. It recommends the fastest measured setup meeting the speed and
context minimums; a speed tie favors more context. Smaller contexts are only
fallback diagnostics when no eligible result passes. A fast 8K result does not
silently meet your long-chat goal. You can select 32K or 8K–16K instead.
This is the fastest among the supported setups tested, not every possible
driver, runtime setting or workload. The legacy 30-tok/s default becomes 50;
custom aspirations are preserved. Change these numbers in Model settings.
The starting reply cap is up to 4,096 tokens, with one answer at a time.

Trials require two completed answers, confirmed GPU use, and the context size
actually accepted by the runner. For 32K and above they also send a large
synthetic input and require reported input-token evidence of at least half the
window. The actual count is shown. This is NOT proof that every full-window
coding job fits or that model recall is perfect.
The same short coding answer is used for the speed comparison.
Writing tokens/sec comes from engine timing. Whole-job speed includes prompt
work and overhead. Missing engine timing cannot certify the writing-speed goal.
Some models cannot reach 25 tokens/sec here; 50+ or 100 is not promised.

FIND YOUR WAY AROUND
Choose your project on the main Models page before starting OpenCode. Choose
folder opens the folder picker; the dropdown remembers up to eight recent
existing folders. Open folder shows the selected directory in File Explorer.
Pick the folder containing the project code, not the folder holding model files.
New terminals start in that folder and show the project and original model name
in their window title and startup text. OpenCode receives the readable model
name while its internal alias and connection stay unchanged. Existing terminals
keep their current project; changing the dropdown affects future launches only.

The sidebar has five pages: Models, Assistant, Running, Performance and
Memory health. Models is the starting page. Select a model, Optimize, then
Start in OpenCode. Detailed controls are in Model settings; extra tuning is
under Advanced settings. Assistant keeps the conversation larger than the
message composer, which you can resize with the divider.

Ollama compares auto, Vulkan and installed ROCm v7.1. Explicit route checks
require actual model-device evidence, not just a GPU library being loaded.
Strata tests a private copy of its existing config, not all expert-cache knobs.
GGUF uses your chosen llama-server.exe and requires full GPU-layer offload.
Missing proof fails honestly. Test GPU setup retains the manual-save flow.

OPENCODE AND SAVED SETTINGS
Hub launches use OpenCode V2 configuration and opencode --standalone in a new
PowerShell terminal. Simple OpenCode is on by default: visible Build and Plan
agents use the chosen local model. Known custom cluster agents and child-agent
spawning are disabled. Hidden maintenance agents use the same local model.
Other model providers are denied in hub sessions. Project instructions and
normal tool permissions remain. Tool-result size is bounded to reduce context
growth. Automatic compaction is enabled. OpenCode gets the model's context limit.
Memory & speed shows reported context used for hub-routed requests.

Original global configs and agents are backed up in the app data folder, not
deleted or rewritten. Simple mode uses a separate config folder. Turning it off
restores normal agent discovery, but hub model requests stay local-only.
Typing opencode in another terminal or using an old session does NOT automatically
use the hub setup. Those sessions keep their previous configuration.
Model, driver, runtime-library or memory-setting changes require a new GPU test.
If the saved identity check differs, Start in OpenCode now offers Recheck and
start. It tests the saved route with current settings, including writing speed
and a large-input check for 32K or more. Only a passing result replaces the
saved identity; a result below the speed minimum needs explicit approval.
Future records retain identity components so changes can be explained. Older
records may not show which component changed. A different hash alone does not
prove the driver changed or the model is broken. Full optimization remains
available when you want to compare routes and context sizes again.
Sampling and reply-length tweaks can be used without the full search; startup
still checks answers and GPU use. Speed is not re-certified for those tweaks.
Undo affects future launches, not an active model.
Private tested servers stop when the hub closes. Other services stay running.

READ THE MEMORY DISPLAY
Low allocation room (commit headroom) is different from low physical RAM.
Windows can refuse a model allocation even when regular RAM is still free.
Memory health > Repair allocation limit keeps graphics memory unchanged and
sets C:\pagefile.sys to 128 GiB initially, with a 192 GiB maximum. It requires
144 GiB of free C: space, administrator approval and a manual Windows restart.
It saves the original setting in data/pagefile-before-repair.json; Undo
allocation repair restores it unless another program changed the setting.
Read the result in the repair console. A copy is saved in
data/pagefile-repair-result.txt. No restart or model stop happens automatically.
This provides disk-backed commit allowance, not more physical RAM or VRAM.
It cannot guarantee speed or fix every GPU allocator/driver failure.
After restarting, use Assistant > Explain my memory to check commit headroom,
then test the model alone. A 96 GB graphics reservation on a 128 GB shared-memory
PC normally leaves roughly 32 GB for Windows; that split is not missing RAM.

Graphics memory setting is the configured figure, such as 96 GB. It is NOT free
GPU memory or a guarantee that one allocation will succeed.
Tracked model GPU use covers reported Ollama models, not all PC graphics use.
Strata GPU use is unknown unless another live reading provides it.
Free GPU memory stays unverified when it cannot be measured reliably.
Unified RAM installed is read from Windows where possible, not guessed by adding
displayed numbers. On the Radeon 8060S, CPU and GPU share physical memory.
Regular Windows RAM is separate. A 32 GB Windows total does not prove a 32 GB
GPU limit. Strata's meminfo guard protects system RAM; do not inflate it to 96 GB.
GB in this app means 1,073,741,824 bytes.

CHAT AND MEMORY FIXES
The introduction no longer fills the conversation box. Your message box starts
at six lines and resizes with the divider above it. Ctrl+Enter sends.
Chat uses an already-running local model. Ollama cloud/remote models are rejected.
No ChatGPT/OpenAI chat calls are made and no ChatGPT allowance is used.
Chat shares resources with coding and may slow it down. Text stays in memory
until cleared/closed. Cancel discards results; sent backend work may continue.
Explain my memory works without a model. It names accessible memory-heavy apps,
reports access limits and separates live readings from saved runtime-log facts.
Ways to help can offer model stop/unload, normal browser close, or lighter future
settings. Save browser work first. Stops interrupt work. Memory is checked again
after stop/close fixes; other apps can affect that reading too.

HELP FROM ANOTHER AI
Copy help report copies measurements, settings, context/speed and test evidence.
It leaves out prompts, keys, command lines and full paths. Nothing uploads itself.
Paste it into your chosen AI. Paste advice from another AI checks the supported
JSON fix plan and shows changes for review. It cannot run pasted shell code or
edit arbitrary files. BIOS, page-file and driver changes are not performed.

COVERAGE AND FILES
Ollama API plus disk manifests/OLLAMA_MODELS; Strata profiles; GGUF files in
AI-Model-Library and common LM Studio folders. Add other GGUF folders in the UI.
Split shards are grouped. Incomplete models and embeddings cannot be coding-chat
models. WSL, containers, remote PCs, renamed engines and Transformers safetensors
are outside launch control. Key-protected servers are not supported by chat.
Profiles, backups, trial logs and metrics live in data/ beside the application.
Request metrics store counts/timing, not prompts. Runtimes/OpenCode log separately.
Keep this folder: the desktop icon points here. Python 3 with Tkinter is needed.
Software checks: python test_monitor.py

LIMITS
Software and installed-OpenCode checks do not prove a real GPU model pass.
The restricted Codex environment could not enumerate the GPU or run its native
TCP-owner check. Use the desktop app in normal Windows for real model tests.
No model is marked as passing without those tests. No app can guarantee every
model/context/speed combination or prevent all driver allocation failures.

REFERENCES
https://docs.ollama.com/api/generate
https://docs.ollama.com/api/create
https://opencode.ai/docs/models/
https://opencode.ai/docs/agents/
https://opencode.ai/docs/config/
Strata flags were checked against the installed CLI and documentation.
