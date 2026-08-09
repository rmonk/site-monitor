// Custom client-side interactivity
document.addEventListener("DOMContentLoaded", function() {
    // Auto-hide alert banners after 5 seconds
    const alerts = document.querySelectorAll(".alert");
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
});
