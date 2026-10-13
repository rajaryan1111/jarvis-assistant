# External Repository Review: ULTRON Voice Stack and Project Media

**Review date:** 2026-10-13  
**Scope:** Research and architecture recommendations only. No external code, model weights, packages, or runtime integrations were copied or installed.  
**Status:** Candidates identified; no candidate is integrated or validated on the target Mac.

## Executive recommendation

1. **Evaluate Handy's design for local speech input**: offline-first transcription, VAD, configurable push-to-talk, model selection, visible recording state, cancellation, and graceful failures. Prefer adapting the principles to ULTRON's own adapter boundary before considering direct integration.
2. **Prototype OmniVoice as an optional TTS adapter**: compare its multilingual speech output against the existing speech path on the actual Apple Silicon Mac. Do not make it the default until language quality, latency, memory, installation footprint, model terms, and offline behavior have been measured.
3. **Keep HyperFrames out of the core voice milestone**: it may be useful later for optional project demo/pitch videos, but it does not solve a core voice assistant requirement.
4. **Do not import external applications wholesale**. Preserve ULTRON's existing central policy, session/task context, approval rules, and HUD. Keep STT, language handling, wake-word detection, and TTS independently replaceable.

## 1. k2-fsa/OmniVoice

Repository: https://github.com/k2-fsa/OmniVoice  
Repository license reported by its public repository: Apache-2.0. Verify any model-specific terms and notices separately before distribution.

### Relevant capabilities
- Multilingual zero-shot text-to-speech with broad language coverage.
- Voice design and reference-audio voice cloning modes.
- Python API and command-line interfaces.
- Apple Silicon/MPS inference instructions are documented by the project.

### ULTRON fit
Potential speech-output backend for spoken answers in Hindi, English, and Hinglish. It may improve naturalness or language coverage, but support claims do not prove acceptable pronunciation of Romanized Hindi, mixed-language utterances, technical vocabulary, file paths, commands, or numbers.

### Risks and unknowns
- PyTorch/model runtime and weights may substantially increase setup size, cold start, memory use, and update complexity.
- Performance figures from NVIDIA GPU tests cannot be assumed for the Mac's Apple Silicon GPU.
- First-run model downloads and offline inference behavior must be measured.
- Voice cloning has privacy, consent, and impersonation risks; it must not be enabled by default.

### Recommendation
**Prototype only, behind a TTS adapter.** Benchmark against the current engine on a fixed test set. Record model revision, terms, setup steps, cold/warm latency, real-time factor, peak memory, output quality, install size, network activity and failure behavior. If results are not compelling, retain the current engine.

Source: https://github.com/k2-fsa/OmniVoice

## 2. cjpais/handy

Repository: https://github.com/cjpais/handy  
Repository license: MIT. The repository notes that Handy's name, logo, icon, and brand assets are not open-source; do not reuse them as ULTRON branding.

### Relevant capabilities and patterns
- Offline-first speech-to-text application.
- Voice activity detection to avoid transcribing long silent regions.
- Push-to-talk/toggle shortcuts and local transcription models.
- Visible desktop controls, transcription history, model configuration, cancellation, and debugging.
- Cross-platform desktop integration with a Rust/Tauri + React/TypeScript architecture and native audio/keyboard integration.

### ULTRON fit
Handy is a strong **reference for interaction and privacy design**. ULTRON should have a dependable keyboard push-to-talk fallback even if the wake word fails, clear recording status, mute/stop/cancel controls, useful permission errors, and typed HUD fallback. VAD and local STT should be independently testable.

### Risks and unknowns
- Handy is a complete application, not automatically a reusable speech library.
- Its CLI flags and app behavior may change; do not treat them as a stable API without verifying the exact version and interface.
- A separate app/subprocess may duplicate microphone ownership, permissions, model storage, lifecycle, or transcription state.
- Offline support depends on the selected model and actual runtime/network behavior.

### Recommendation
**Adopt design principles, not a wholesale dependency.** First map ULTRON's current microphone/STT flow. Compare the current implementation with Handy's VAD, local model, hotkey, cancellation and accessibility patterns. Consider direct process/IPC integration only if it has a clear advantage and a tested, documented interface.

Source: https://github.com/cjpais/handy

## 3. HeyGen and HyperFrames

User-supplied URL: https://github.com/heygen-comP  
That URL did not identify a specific repository during this review. A possible intended source is the HeyGen organization repository https://github.com/heygen-com/hyperframes; confirm before treating this as the requested project.

### Relevant capabilities
HyperFrames describes a deterministic HTML/CSS/media-based video composition and rendering workflow, including agent-oriented skills and repeatable rendering.

### ULTRON fit
Potentially useful later to turn a project's approved screenshots, demo script, captions and narration into a polished hackathon or portfolio video. This is a project-artifact capability, not speech recognition, wake-word detection, or core assistant reasoning.

### Risks and recommendation
Keep it **optional and low priority**. Do not add Node/FFmpeg/browser-rendering dependencies to the core assistant. If the user confirms this was the intended repository and requests demo-video generation, evaluate the license, dependencies, generated assets, and review-before-render workflow in a separate milestone.

Source: https://github.com/heygen-com/hyperframes

## 4. Proposed voice architecture

Keep these interfaces separate:

1. **Audio capture and VAD** — local audio capture, bounded recording, silence handling and device/permission errors.
2. **STT adapter** — transcript, language/script metadata when available, confidence/timing when supported, and typed failures.
3. **Language handling** — preserve Hindi, English, Romanized Hindi, Devanagari and code-switching.
4. **Wake-word adapter** — local “Ultron” detection, independently controllable and not dependent on cloud streaming.
5. **TTS adapter** — text + language/settings in; audio or a typed failure out.
6. **Playback/session coordinator** — interruption, cancellation, timeouts, device changes, and continuity between speech and HUD.

External models must remain optional so ordinary CLI/API and text workflows do not require downloading large ML weights.

## 5. Evaluation plan and exit criteria

### Fixed test set
Include:
- English technical explanations.
- Hindi in Devanagari.
- Romanized Hindi.
- Natural Hindi-English code-switching.
- Technical names, acronyms, numbers, filenames, paths and command snippets.
- Quiet speech, pauses, background noise, empty input and ambiguous commands.

Use only recordings the user has consented to use; avoid storing raw voice samples by default.

### Measurements
- Word/character error rate or manually reviewed transcript accuracy, split by language/script.
- Pronunciation and intelligibility ratings for generated speech, split by language and code-switching.
- Cold-start and warm per-utterance latency.
- Real-time factor where applicable, peak memory, download size, installed footprint and CPU/GPU utilization.
- Network access required during setup and during inference.
- Behavior on missing permissions, missing model, no network, cancellation, timeout, interrupted playback and low-confidence transcription.

### Security and privacy checks
- Show when the microphone is active and provide immediate stop/mute/cancel.
- No continuous cloud audio streaming just to detect the wake word.
- Disclose provider and data sent before remote speech processing.
- Voice cloning off by default; require permission for the source voice, clear data handling, and deletion controls.
- Recognition confidence never authorizes consequential actions; ambiguous requests require clarification.
- Verify repository license, model license/terms, notices, dependencies and supported platforms before distribution.

### Decision rule
A candidate becomes **prototype** only after a bounded adapter and mocked contract tests exist. It becomes **verified** only after reproducible target-Mac tests and measured results. Until then, status is **candidate / not integrated**.

## Sources
- OmniVoice: https://github.com/k2-fsa/OmniVoice
- Handy: https://github.com/cjpais/handy
- HeyGen HyperFrames (possible intended source): https://github.com/heygen-com/hyperframes
