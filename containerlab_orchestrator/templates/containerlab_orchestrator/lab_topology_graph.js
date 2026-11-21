(function () {
    const LABEL_STYLE = {
        show: true,
        position: "bottom",
        color: "#1a1a1a",
        backgroundColor: "#ffffff",
        padding: [2, 4],
        borderRadius: 2,
    };
    const LINE_STYLE = {color: "#a0a4ab", width: 1.5};
    const TOOLTIP_CSS =
        "background-color: var(--bs-body-bg); color: var(--bs-body-color); border-color: var(--bs-border-color);";
    const ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};

    function escapeText(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => ENTITIES[character]);
    }

    function orNone(value) {
        return value === "" || value == null ? "None" : value;
    }

    function description(data) {
        const groupTexts = Object.keys(data.groups).map((key) => {
            const names = data.nodes.filter((node) => node.group === key).map((node) => node.name);
            return `${data.groups[key].label}: ${names.join(", ") || "None"}.`;
        });
        return [`Topology graph: ${data.nodes.length} nodes, ${data.links.length} links.`, ...groupTexts].join(" ");
    }

    function tooltipText(params) {
        if (params.dataType === "edge") {
            return escapeText(params.data.endpointsText);
        }
        const info = params.data.info;
        return [
            ["Name", params.data.name],
            ["Kind", info.kind],
            ["Image", info.image],
            ["Management IPv4", info.mgmt_ipv4],
            ["State", info.state],
        ]
            .map(([label, value]) => `${label}: ${escapeText(orNone(value))}`)
            .join("<br>");
    }

    function buildOption(data) {
        return {
            darkMode: document.documentElement.dataset.bsTheme === "dark",
            aria: {enabled: true, label: {description: description(data)}},
            tooltip: {formatter: tooltipText, extraCssText: TOOLTIP_CSS},
            series: [
                {
                    type: "graph",
                    layout: "force",
                    roam: true,
                    draggable: true,
                    autoCurveness: true,
                    force: {repulsion: 200, edgeLength: 80, layoutAnimation: true},
                    label: LABEL_STYLE,
                    lineStyle: LINE_STYLE,
                    data: data.nodes.map((node) => ({
                        name: node.name,
                        info: node.info,
                        symbol: node.stub ? "rect" : "circle",
                        symbolSize: 24,
                        itemStyle: {color: data.groups[node.group].color},
                    })),
                    links: data.links.map((link) => ({
                        source: link.source,
                        target: link.target,
                        endpointsText: link.endpoints_text,
                    })),
                },
            ],
        };
    }

    function start() {
        const element = document.querySelector("[data-no-library-text]");
        if (!element) {
            return;
        }
        const data = JSON.parse(document.getElementById(`echarts-config-${element.id}`).textContent);
        let chart = null;
        const observer = new ResizeObserver((entries) => {
            if (entries[0].contentRect.width <= 0) {
                return;
            }
            if (chart) {
                chart.resize();
                return;
            }
            try {
                chart = window.echarts.init(element, null, {renderer: "canvas"});
                chart.setOption(buildOption(data));
            } catch {
                chart?.dispose();
                observer.disconnect();
                element.textContent = element.dataset.noLibraryText;
            }
        });
        observer.observe(element);
    }

    document.addEventListener("DOMContentLoaded", start);
})();
