# Model Monitor

A Windows desktop control hub for local AI models: inspect memory, discover models, test GPU setups and context/speed settings, chat locally, and launch OpenCode.

## Download and run

1. Download **Model-Monitor-Windows-v0.1.0.zip** from [Releases](https://github.com/mathiasmednick/model-monitor/releases/latest).
2. Right-click the ZIP and choose **Extract All**. Put the folder somewhere you can write to, such as Documents.
3. Install [Python for Windows](https://www.python.org/downloads/windows/) with Tkinter (included in the standard installer). This version was checked with Python 3.14.
4. Double-click **Start Model Monitor.cmd** inside the extracted folder.

This initial release contains Python source, not a standalone EXE. There are no third-party Python package dependencies. Windows only; Mac and Linux are not supported.

Install your preferred supported local runtime separately: Ollama, Strata, or llama-server for GGUF files. Install OpenCode separately if you want to launch coding sessions. Models, drivers, runtimes and OpenCode are not included. Hardware and available memory determine which models will work.

## Saved settings

The app creates a `data` folder beside its files for local settings, profiles, logs and backups. Your own models and personal settings are not included in this download. Keep the extracted folder together. See [README.txt](README.txt) for the detailed feature guide.

## System changes

The optional **Repair allocation limit** action changes the Windows C: page-file setting to 128 GB initially and 192 GB maximum. It requires administrator approval, sufficient disk space and a restart; an Undo option is provided. It does not add RAM or VRAM. Read the app confirmation before using this hardware-specific option.

## Verification

Run `python test_monitor.py` from this folder. Software tests do not certify GPU compatibility, model fit, speed or full-window context behavior on another PC. This is an early release; test with your own setup.

Known issue in this preview: 85 of 86 existing tests passed on the publishing PC. The installed-OpenCode integration test did not find the expected visible Build and Plan agents. OpenCode integration needs further verification.
