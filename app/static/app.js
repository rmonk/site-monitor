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

document.addEventListener("DOMContentLoaded", function() {
    // 1. Initial Theme Application
    updateTheme();

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
