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
