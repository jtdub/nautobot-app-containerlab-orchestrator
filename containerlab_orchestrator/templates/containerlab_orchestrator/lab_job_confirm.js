(function () {
    const button = document.getElementById("component-{{ component.component_id }}");
    if (button instanceof HTMLAnchorElement) {
        button.addEventListener("click", function (event) {
            if (!window.confirm("{{ confirm_text|escapejs }}")) {
                event.preventDefault();
            }
        });
    }
})();
