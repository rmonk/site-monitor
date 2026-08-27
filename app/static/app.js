// Site Monitor - Client Side Scripts & Theme Handling

function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function updateTheme() {
    const html = document.documentElement;
    const themeMode = html.getAttribute('data-theme-mode') || 'light';

    if (themeMode === 'system') {
        html.setAttribute('data-bs-theme', getSystemTheme());
    } else {
        html.setAttribute('data-bs-theme', themeMode);
    }
}

// Time Display Mode (UTC / Local) Handling

function getTimeDisplayMode() {
    let mode = null;
    try {
        mode = localStorage.getItem('time_display');
    } catch (e) {}
    return (mode === 'utc' || mode === 'local') ? mode : (document.documentElement.getAttribute('data-time-display') || 'utc');
}

function setTimeDisplayMode(mode) {
    if (mode !== 'utc' && mode !== 'local') return;
    try {
        localStorage.setItem('time_display', mode);
    } catch (e) {}
    document.cookie = `time_display=${mode}; path=/; max-age=31536000; SameSite=Lax`;
    document.documentElement.setAttribute('data-time-display', mode);

    const label = document.getElementById('currentTimeDisplayLabel');
    if (label) {
        label.textContent = mode === 'local' ? 'Local' : 'UTC';
    }

    const utcBtn = document.querySelector('.time-option-utc');
    const localBtn = document.querySelector('.time-option-local');
    if (utcBtn) utcBtn.classList.toggle('active', mode === 'utc');
    if (localBtn) localBtn.classList.toggle('active', mode === 'local');

    document.querySelectorAll('.checkmark-utc').forEach(el => el.classList.toggle('d-none', mode !== 'utc'));
    document.querySelectorAll('.checkmark-local').forEach(el => el.classList.toggle('d-none', mode !== 'local'));

    updateTimestamps();
}

function formatTimestamp(rawStr, mode) {
    if (!rawStr) return '';
    let str = String(rawStr).trim();
    if (!str) return '';

    let date;
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(str)) {
        date = new Date(str.replace(' ', 'T') + 'Z');
    } else if (str.endsWith(' UTC')) {
        date = new Date(str.slice(0, -4).replace(' ', 'T') + 'Z');
    } else {
        date = new Date(str);
    }

    if (isNaN(date.getTime())) return rawStr;

    const pad = (n) => String(n).padStart(2, '0');
    if (mode === 'local') {
        const year = date.getFullYear();
        const month = pad(date.getMonth() + 1);
        const day = pad(date.getDate());
        const hours = pad(date.getHours());
        const mins = pad(date.getMinutes());
        const secs = pad(date.getSeconds());
        return `${year}-${month}-${day} ${hours}:${mins}:${secs}`;
    } else {
        const year = date.getUTCFullYear();
        const month = pad(date.getUTCMonth() + 1);
        const day = pad(date.getUTCDate());
        const hours = pad(date.getUTCHours());
        const mins = pad(date.getUTCMinutes());
        const secs = pad(date.getUTCSeconds());
        return `${year}-${month}-${day} ${hours}:${mins}:${secs} UTC`;
    }
}

function updateTimestamps() {
    const mode = getTimeDisplayMode();
    document.querySelectorAll('.app-timestamp').forEach(el => {
        const raw = el.getAttribute('data-utc');
        if (raw) {
            el.textContent = formatTimestamp(raw, mode);
        }
    });

    if (window.statusChartInstance && window.rawChartHistory) {
        window.statusChartInstance.data.labels = window.rawChartHistory.map(item => formatTimestamp(item.timestamp, mode));
        window.statusChartInstance.update();
    }
}

// Explicitly bind to global window object
window.getSystemTheme = getSystemTheme;
window.updateTheme = updateTheme;
window.getTimeDisplayMode = getTimeDisplayMode;
window.setTimeDisplayMode = setTimeDisplayMode;
window.formatTimestamp = formatTimestamp;
window.updateTimestamps = updateTimestamps;

// Attach click listener immediately for timezone buttons (delegated)
document.addEventListener("click", function(e) {
    const timeBtn = e.target.closest("[data-time-mode]");
    if (timeBtn) {
        const mode = timeBtn.getAttribute("data-time-mode");
        if (mode === 'utc' || mode === 'local') {
            setTimeDisplayMode(mode);
        }
    }
});

document.addEventListener("DOMContentLoaded", function() {
    // 1. Initial Theme & Time Display Application
    updateTheme();
    setTimeDisplayMode(getTimeDisplayMode());

    // Listen for OS theme changes if in system mode
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
        if (document.documentElement.getAttribute('data-theme-mode') === 'system') {
            updateTheme();
        }
    });

    // 2. Auto-hide alert banners after 5 seconds
    const alerts = document.querySelectorAll(".alert");
    alerts.forEach(function(alert) {
        setTimeout(function() {
            try {
                const bsAlert = new bootstrap.Alert(alert);
                bsAlert.close();
            } catch (e) {}
        }, 5000);
    });

    // 3. Theme & Custom Color Picker Live Preview on Settings Page
    const presetCustomRadio = document.getElementById("preset_custom");
    const customOptionsDiv = document.getElementById("custom_color_options");

    function toggleCustomOptions() {
        if (customOptionsDiv) {
            const isCustom = presetCustomRadio && presetCustomRadio.checked;
            customOptionsDiv.style.display = isCustom ? "block" : "none";
        }
    }

    if (presetCustomRadio) {
        document.querySelectorAll('input[name="theme_color_preset"]').forEach(radio => {
            radio.addEventListener('change', function() {
                toggleCustomOptions();
                document.documentElement.setAttribute('data-theme-preset', this.value);
            });
        });
        toggleCustomOptions();
    }

    // Live sync between color pickers and text inputs, and update CSS root variables
    const customPrimaryPicker = document.getElementById("theme_custom_primary_picker");
    const customPrimaryText = document.getElementById("theme_custom_primary");
    const customBgPicker = document.getElementById("theme_custom_bg_picker");
    const customBgText = document.getElementById("theme_custom_bg");
    const customCardPicker = document.getElementById("theme_custom_card_picker");
    const customCardText = document.getElementById("theme_custom_card");
    const customTextPicker = document.getElementById("theme_custom_text_picker");
    const customTextText = document.getElementById("theme_custom_text");

    function syncColor(picker, text, varName) {
        if (!picker || !text) return;
        picker.addEventListener("input", function() {
            text.value = picker.value;
            document.documentElement.style.setProperty(varName, picker.value);
        });
        text.addEventListener("input", function() {
            if (text.value.startsWith("#") && (text.value.length === 4 || text.value.length === 7)) {
                picker.value = text.value;
                document.documentElement.style.setProperty(varName, text.value);
            }
        });
    }

    syncColor(customPrimaryPicker, customPrimaryText, "--custom-primary");
    syncColor(customBgPicker, customBgText, "--custom-bg");
    syncColor(customCardPicker, customCardText, "--custom-card");
    syncColor(customTextPicker, customTextText, "--custom-text");

    // Theme Mode Radio Live Switch
    document.querySelectorAll('input[name="theme_mode"]').forEach(radio => {
        radio.addEventListener('change', function() {
            document.documentElement.setAttribute('data-theme-mode', this.value);
            updateTheme();
        });
    });
});

// ==========================================
// Passkeys & WebAuthn Client Helpers
// ==========================================

function bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function base64urlToBuffer(base64url) {
    let base64 = String(base64url).replace(/-/g, '+').replace(/_/g, '/');
    while (base64.length % 4) {
        base64 += '=';
    }
    const binary = window.atob(base64);
    const buffer = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        buffer[i] = binary.charCodeAt(i);
    }
    return buffer.buffer;
}

function prepareRegistrationOptions(options) {
    const publicKey = { ...options };
    if (typeof publicKey.challenge === 'string') {
        publicKey.challenge = base64urlToBuffer(publicKey.challenge);
    }
    if (publicKey.user && typeof publicKey.user.id === 'string') {
        publicKey.user = {
            ...publicKey.user,
            id: base64urlToBuffer(publicKey.user.id),
        };
    }
    if (Array.isArray(publicKey.excludeCredentials)) {
        publicKey.excludeCredentials = publicKey.excludeCredentials.map(cred => ({
            ...cred,
            id: typeof cred.id === 'string' ? base64urlToBuffer(cred.id) : cred.id,
        }));
    }
    return publicKey;
}

function serializeRegistrationCredential(credential) {
    const response = credential.response;
    const clientExtensionResults = credential.getClientExtensionResults ? credential.getClientExtensionResults() : {};
    
    let transports = [];
    if (response.getTransports) {
        transports = response.getTransports();
    }

    return {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        type: credential.type,
        response: {
            clientDataJSON: bufferToBase64url(response.clientDataJSON),
            attestationObject: bufferToBase64url(response.attestationObject),
            transports: transports,
        },
        clientExtensionResults: clientExtensionResults,
    };
}

function prepareAuthenticationOptions(options) {
    const publicKey = { ...options };
    if (typeof publicKey.challenge === 'string') {
        publicKey.challenge = base64urlToBuffer(publicKey.challenge);
    }
    if (Array.isArray(publicKey.allowCredentials)) {
        publicKey.allowCredentials = publicKey.allowCredentials.map(cred => ({
            ...cred,
            id: typeof cred.id === 'string' ? base64urlToBuffer(cred.id) : cred.id,
        }));
    }
    return publicKey;
}

function serializeAuthenticationCredential(credential) {
    const response = credential.response;
    const clientExtensionResults = credential.getClientExtensionResults ? credential.getClientExtensionResults() : {};

    return {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        type: credential.type,
        response: {
            clientDataJSON: bufferToBase64url(response.clientDataJSON),
            authenticatorData: bufferToBase64url(response.authenticatorData),
            signature: bufferToBase64url(response.signature),
            userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : null,
        },
        clientExtensionResults: clientExtensionResults,
    };
}

async function initiatePasskeyRegistration() {
    const alertBox = document.getElementById("passkeyRegisterAlert");
    if (alertBox) {
        alertBox.classList.add("d-none");
        alertBox.textContent = "";
    }

    if (!window.PublicKeyCredential) {
        if (alertBox) {
            alertBox.textContent = "Passkeys / WebAuthn is not supported by your browser or requires a secure HTTPS connection.";
            alertBox.classList.remove("d-none");
        } else {
            alert("Passkeys are not supported by this browser.");
        }
        return;
    }

    const customName = prompt("Enter a name for this Passkey (e.g. 'MacBook Touch ID', 'iPhone Face ID', 'YubiKey'):", "My Passkey");
    if (customName === null) return; // User clicked Cancel

    try {
        // 1. Request registration options from backend
        const optionsRes = await fetch("/api/passkeys/register/options", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });

        if (!optionsRes.ok) {
            const err = await optionsRes.json().catch(() => ({}));
            throw new Error(err.detail || "Failed to initiate passkey registration.");
        }

        const { options, challenge_id } = await optionsRes.json();
        const publicKey = prepareRegistrationOptions(options);

        // 2. Prompt browser authenticator
        const credential = await navigator.credentials.create({ publicKey });
        if (!credential) {
            throw new Error("No credential was created by the authenticator.");
        }

        const serializedCredential = serializeRegistrationCredential(credential);

        // 3. Send response to backend for verification
        const verifyRes = await fetch("/api/passkeys/register/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                response: serializedCredential,
                challenge_id: challenge_id,
                name: customName || "Passkey",
            }),
        });

        const verifyData = await verifyRes.json().catch(() => ({}));
        if (!verifyRes.ok || !verifyData.success) {
            throw new Error(verifyData.detail || "Failed to verify passkey with server.");
        }

        window.location.href = "/settings?msg=" + encodeURIComponent(verifyData.message || "Passkey registered successfully!") + "&type=success";
    } catch (err) {
        console.error("Passkey registration error:", err);
        if (alertBox) {
            alertBox.textContent = err.name === "NotAllowedError" ? "Passkey registration was cancelled or timed out." : (err.message || "Error registering passkey.");
            alertBox.classList.remove("d-none");
        } else {
            alert("Error registering passkey: " + err.message);
        }
    }
}

async function loginWithPasskey() {
    const alertBox = document.getElementById("passkeyLoginAlert");
    const loginBtn = document.getElementById("passkeyLoginBtn");

    if (alertBox) {
        alertBox.classList.add("d-none");
        alertBox.textContent = "";
    }

    if (!window.PublicKeyCredential) {
        if (alertBox) {
            alertBox.textContent = "Passkey sign-in is not supported on this browser or requires a secure HTTPS connection.";
            alertBox.classList.remove("d-none");
        }
        return;
    }

    const usernameInput = document.getElementById("username");
    const username = usernameInput ? usernameInput.value.trim() : null;

    try {
        if (loginBtn) {
            loginBtn.disabled = true;
            loginBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span> Waiting for biometrics/key...';
        }

        // 1. Fetch authentication options from backend
        const optionsRes = await fetch("/api/auth/passkey/options", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: username || undefined }),
        });

        if (!optionsRes.ok) {
            const err = await optionsRes.json().catch(() => ({}));
            throw new Error(err.detail || "Failed to initiate passkey sign-in.");
        }

        const { options, challenge_id } = await optionsRes.json();
        const publicKey = prepareAuthenticationOptions(options);

        // 2. Prompt browser authenticator
        const assertion = await navigator.credentials.get({ publicKey });
        if (!assertion) {
            throw new Error("No passkey assertion returned by authenticator.");
        }

        const serializedAssertion = serializeAuthenticationCredential(assertion);

        // 3. Verify assertion on backend
        const verifyRes = await fetch("/api/auth/passkey/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                response: serializedAssertion,
                challenge_id: challenge_id,
            }),
        });

        const verifyData = await verifyRes.json().catch(() => ({}));
        if (!verifyRes.ok || !verifyData.success) {
            throw new Error(verifyData.detail || "Passkey authentication failed.");
        }

        window.location.href = verifyData.redirect_url || "/";
    } catch (err) {
        console.error("Passkey login error:", err);
        if (alertBox) {
            alertBox.textContent = err.name === "NotAllowedError" ? "Passkey sign-in was cancelled or timed out." : (err.message || "Passkey authentication failed.");
            alertBox.classList.remove("d-none");
        }
    } finally {
        if (loginBtn) {
            loginBtn.disabled = false;
            loginBtn.innerHTML = '<i class="bi bi-fingerprint fs-5"></i><span>Sign in with Passkey</span>';
        }
    }
}

// Bind passkey functions to window
window.initiatePasskeyRegistration = initiatePasskeyRegistration;
window.loginWithPasskey = loginWithPasskey;
