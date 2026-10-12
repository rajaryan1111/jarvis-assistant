// HUD sign-in. Exchanges either a one-time login ticket (from `python -m ultron open-hud`,
// passed in the URL *fragment* so it never reaches server logs) or the pasted API token
// for an HttpOnly session cookie. Nothing is stored in localStorage.
document.addEventListener("DOMContentLoaded", () => {
    const form   = document.getElementById("login-form");
    const input  = document.getElementById("login-token");
    const status = document.getElementById("login-status");

    function setStatus(text, isError) {
        if (!status) return;
        status.textContent = text;
        status.classList.toggle("login-error", Boolean(isError));
    }

    async function login(payload) {
        setStatus("Signing in…", false);
        try {
            const res = await fetch("/login", {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify(payload),
            });
            let data = {};
            try { data = await res.json(); } catch (e) { data = {}; }
            if (res.ok && data.ok) {
                window.location.replace("/");
                return;
            }
            setStatus(data.message || ("Sign-in failed (" + res.status + ")."), true);
        } catch (e) {
            setStatus("Could not reach the JARVIS server.", true);
        }
    }

    // One-time ticket in the fragment: consume it and scrub it from the address bar/history.
    const match = window.location.hash.match(/^#ticket=([A-Za-z0-9._-]+)$/);
    if (match) {
        history.replaceState(null, "", window.location.pathname);
        login({ ticket: match[1] });
    }

    if (form && input) {
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const token = input.value.trim();
            input.value = "";
            if (token) login({ token });
        });
    }
});
