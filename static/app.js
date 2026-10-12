// ========================
//   J.A.R.V.I.S WEB HUD
// ========================

document.addEventListener("DOMContentLoaded", () => {
    console.log("JARVIS app.js loaded");

    // ---- DOM refs ----
    const bootScreen    = document.getElementById("boot-screen");
    const hud           = document.getElementById("hud");
    const chatBox       = document.getElementById("chat-box");
    const hudTime       = document.getElementById("hud-time");
    const hudBattery    = document.getElementById("hud-battery");
    const redAlertAudio = document.getElementById("red-alert-sound");

    const textInput     = document.getElementById("text-command");
    const sendButton    = document.getElementById("send-command");

    const WAKE_WORD = "jarvis";

    // --------------------------
    //   AUTHENTICATED API ACCESS
    // --------------------------
    // The HUD is authenticated by an HttpOnly session cookie set at /login. State-changing
    // requests also carry the per-session CSRF token from the page. No API token or key is
    // ever present in this file or the page.
    const csrfMeta  = document.querySelector('meta[name="csrf-token"]');
    const CSRF_TOKEN = csrfMeta ? csrfMeta.getAttribute("content") : "";

    async function apiFetch(path, { method = "GET", body } = {}) {
        const headers = { "Accept": "application/json" };
        const init = { method, headers, credentials: "same-origin", cache: "no-store" };
        if (method !== "GET") {
            headers["Content-Type"] = "application/json";
            headers["X-CSRF-Token"] = CSRF_TOKEN;
            init.body = JSON.stringify(body || {});
        }
        const res = await fetch(path, init);
        if (res.status === 401) {
            window.location.assign("/login");
            throw new Error("Session expired");
        }
        let data = {};
        try { data = await res.json(); } catch (e) { data = {}; }
        if (!res.ok) {
            const err = new Error(data.message || ("Request failed (" + res.status + ")"));
            err.status = res.status;
            throw err;
        }
        return data;
    }

    // --------------------------
    //   BASIC CHAT UI HELPERS
    // --------------------------
    function addMessage(text, sender) {
        if (!chatBox || !text) return;
        const div = document.createElement("div");
        div.className = sender === "user" ? "msg-user" : "msg-jarvis";
        div.textContent = (sender === "user" ? "YOU: " : "JARVIS: ") + text;
        chatBox.appendChild(div);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    // --------------------------
    //   SPEECH: ONLY MAC SPEAKS
    // --------------------------
    function speak(text) {
        if (!text) return;

        // Always ask backend (Mac) to speak
        apiFetch("/speak", { method: "POST", body: { text: text.slice(0, 1000) } })
            .catch((e) => console.log("Speech request error:", e.message));

        // Old browser speech logic is intentionally NOT executed
        // to avoid double-speaking on phone.
        // (Kept as comment so code size is not reduced.)
        /*
        if (!("speechSynthesis" in window) || !text) return;
        try {
            speechSynthesis.cancel();
            const utter = new SpeechSynthesisUtterance(text);
            utter.rate = 1.0;
            utter.pitch = 0.95;
            speechSynthesis.speak(utter);
        } catch (e) {
            console.log("Speech error:", e);
        }
        */
    }

    // --------------------------
    //   RED ALERT SOUND HELPERS
    // --------------------------
    function playRedAlertSound() {
        if (!redAlertAudio) return;
        try {
            redAlertAudio.currentTime = 0;
            redAlertAudio.loop = true;
            redAlertAudio.play();
        } catch (e) {
            console.log("Red alert sound error:", e);
        }
    }

    function stopRedAlertSound() {
        if (!redAlertAudio) return;
        try {
            redAlertAudio.pause();
            redAlertAudio.currentTime = 0;
        } catch (e) {
            console.log("Red alert sound stop error:", e);
        }
    }

    // --------------------------
    //   BACKEND COMMUNICATION
    // --------------------------
    // source "voice" marks speech-recognised commands: the server then applies the voice trust
    // rules (voice can ask for high-impact actions but never approve them).
    async function sendToJarvis(text, source) {
        if (!text) return;
        addMessage(text, "user");

        try {
            const body = { message: text };
            if (source === "voice") body.input = "voice";
            const data = await apiFetch("/ask", { method: "POST", body: body });
            const reply = data.reply || "I did not get a reply from the server.";
            addMessage(reply, "jarvis");
            if (data.pending_action) {
                showPendingAction(data.pending_action);
            }

            // Mac speaks reply (once)
            speak(reply);
        } catch (err) {
            console.error("Backend error:", err.message);
            if (err.message === "Session expired") return;
            addMessage(err.status ? err.message : "There was an error contacting the server.", "jarvis");
        }
    }

    // --------------------------
    //   PENDING ACTION CONFIRMATION
    // --------------------------
    // Destructive actions (shutdown, restart, cache cleanup, clearing reminders) only run
    // after the user confirms *this specific* pending action before it expires. Requests made
    // by voice (wake word) or API clients can only be approved here, so they are polled.
    const shownPending = new Set();

    function showPendingAction(pending) {
        if (!chatBox || !pending || !pending.action_id) return;
        if (shownPending.has(pending.action_id)) return;
        shownPending.add(pending.action_id);
        const box = document.createElement("div");
        box.className = "pending-action";

        const label = document.createElement("div");
        label.className = "pending-action-text";
        const via = pending.requested_via && pending.requested_via !== "hud"
            ? " [requested by " + pending.requested_via + "]" : "";
        label.textContent = "CONFIRM: " + (pending.summary || pending.tool || "pending action") + via;
        box.appendChild(label);

        const buttons = document.createElement("div");
        buttons.className = "pending-action-buttons";
        const confirmBtn = document.createElement("button");
        confirmBtn.type = "button";
        confirmBtn.className = "pending-confirm";
        confirmBtn.textContent = "CONFIRM";
        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "pending-cancel";
        cancelBtn.textContent = "CANCEL";
        buttons.appendChild(confirmBtn);
        buttons.appendChild(cancelBtn);
        box.appendChild(buttons);
        chatBox.appendChild(box);
        chatBox.scrollTop = chatBox.scrollHeight;

        const ttlMs = Math.max(0, (pending.expires_in || 0) * 1000);
        const expiry = setTimeout(() => finish("(expired)"), ttlMs);

        function finish(note) {
            clearTimeout(expiry);
            confirmBtn.disabled = true;
            cancelBtn.disabled = true;
            box.classList.add("pending-done");
            if (note) label.textContent += " " + note;
        }

        async function resolve(path, payload) {
            finish();
            try {
                const data = await apiFetch(path, { method: "POST", body: payload });
                if (data.reply) {
                    addMessage(data.reply, "jarvis");
                    speak(data.reply);
                }
            } catch (err) {
                if (err.message !== "Session expired") addMessage(err.message, "jarvis");
            }
        }

        confirmBtn.addEventListener("click", () => resolve("/confirm", { action_id: pending.action_id }));
        cancelBtn.addEventListener("click", () => resolve("/cancel", { action_id: pending.action_id }));
    }

    async function pollPending() {
        if (document.hidden) return;
        try {
            const data = await apiFetch("/pending");
            (data.pending || []).forEach(showPendingAction);
        } catch (e) {
            // ignore errors
        }
    }

    setInterval(pollPending, 3000);

    // --------------------------
    //   STATUS POLLING (HUD)
    // --------------------------
    async function pollStatus() {
        try {
            const data = await apiFetch("/status");
            if (hudTime && data.time) {
                hudTime.textContent = data.time;
            }
            if (hudBattery && data.battery != null) {
                hudBattery.textContent = data.battery + "%";
            }
        } catch (e) {
            // ignore errors
        }
    }

    setInterval(pollStatus, 5000);
    pollStatus();

    // --------------------------
    //   VOICE RECOGNITION
    // --------------------------
    let recognition = null;
    let listening = false;

    // Soft reset helper: stop current session so browser can restart fresh
    function resetRecognitionSoft() {
        if (!recognition) return;
        try {
            console.log("Soft resetting recognition session...");
            recognition.stop();
            // onend handler will flip listening=false and restart
        } catch (e) {
            console.log("Recognition stop error:", e);
        }
    }

    function startListening() {
        if (!recognition || listening) return;
        try {
            recognition.start();
            listening = true;
            console.log("Voice recognition started");
        } catch (e) {
            console.log("Recognition start error:", e);
        }
    }

    if ("webkitSpeechRecognition" in window) {
        recognition = new webkitSpeechRecognition();
        recognition.lang = "en-IN";
        recognition.continuous = true;
        recognition.interimResults = false;

        recognition.onresult = (event) => {
            const transcript = event.results[event.resultIndex][0].transcript
                .trim()
                .toLowerCase();
            console.log("Heard:", transcript);

            if (!transcript.includes(WAKE_WORD)) return;

            const cmd = transcript.split(WAKE_WORD).slice(1).join(" ").trim();

            // Only wake word detected
            if (!cmd) {
                const line = "Yes sir?";
                addMessage(line, "jarvis");
                speak(line);
                // Soft reset after answer so mic session stays fresh
                resetRecognitionSoft();
                return;
            }

            // RED ALERT MODE
            if (cmd.includes("red alert")) {
                document.body.classList.add("red-alert");
                playRedAlertSound();
                const line = "Red alert protocol activated, sir.";
                addMessage(line, "jarvis");
                speak(line);
                // Reset session after handling
                resetRecognitionSoft();
                return;
            }

            if (cmd.includes("normal mode") || cmd.includes("stand down")) {
                document.body.classList.remove("red-alert");
                stopRedAlertSound();
                const line = "Standing down from red alert, sir.";
                addMessage(line, "jarvis");
                speak(line);
                // Reset session after handling
                resetRecognitionSoft();
                return;
            }

            // Normal command → backend
            sendToJarvis(cmd, "voice");

            // 🔁 IMPORTANT:
            // After every full command, reset recognition so browser
            // does not get stuck after several commands.
            resetRecognitionSoft();
        };

        recognition.onend = () => {
            console.log("Recognition ended");
            listening = false;
            // auto-restart after a short pause
            setTimeout(startListening, 600);
        };

        recognition.onerror = (e) => {
            console.log("Recognition error:", e);
            listening = false;
            // give browser a moment, then try again
            setTimeout(startListening, 1200);
        };

        // Extra watchdog: if for any reason listening is false for too long, restart
        setInterval(() => {
            if (!recognition) return;
            if (!listening) {
                console.log("Watchdog: recognition not listening, trying restart...");
                startListening();
            }
        }, 10000);
    } else {
        const line = "Browser does not support speech recognition, sir.";
        addMessage(line, "jarvis");
        speak(line);
    }

    // --------------------------
    //   TYPED COMMANDS (PHONE / DESKTOP)
    // --------------------------
    function sendTypedCommand() {
        if (!textInput) return;
        const value = textInput.value.trim();
        if (!value) return;
        textInput.value = "";

        // Do NOT speak here to avoid double-speaking.
        console.log("Typed command sent, waiting for Jarvis reply...");

        // Send to backend
        sendToJarvis(value);
    }

    if (sendButton && textInput) {
        sendButton.addEventListener("click", () => {
            sendTypedCommand();
            textInput.focus();
        });

        textInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                sendTypedCommand();
            }
        });
    }

    // --------------------------
    //   BOOT SEQUENCE HANDLING
    // --------------------------
    function finishBoot() {
        if (bootScreen) {
            bootScreen.style.display = "none";
        }
        if (hud) {
            hud.classList.remove("hidden");
            hud.style.display = "block";
        }

        const bootLine = "JARVIS system online. Welcome back, sir. Say Jarvis, then your command.";

        addMessage("SYSTEM: Audio link engaged. I will speak all responses through your laptop speakers.", "jarvis");
        addMessage("SYSTEM: Voice channel active. Say 'Jarvis' then your command, or type below.", "jarvis");
        addMessage("SYSTEM: " + bootLine, "jarvis");
        speak(bootLine);

        startListening();
    }

    // Show HUD after 3.5 seconds
    setTimeout(finishBoot, 3500);
});
