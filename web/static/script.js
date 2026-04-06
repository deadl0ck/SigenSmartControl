document.addEventListener('DOMContentLoaded', () => {
    const MODE_LABELS = {
        SELF_POWERED: 'Self Powered',
        AI: 'AI',
        TOU: 'Time of Use',
        GRID_EXPORT: 'Grid Export',
        REMOTE_EMS: 'Remote EMS',
        CUSTOM: 'Custom'
    };

    const MODE_EXPLANATIONS = {
        SELF_POWERED: 'Prioritizes using solar and battery to reduce grid import.',
        AI: 'Lets Sigen optimize operation automatically.',
        TOU: 'Optimizes behavior around time-of-use tariff windows.',
        GRID_EXPORT: 'Prioritizes exporting available energy to the grid.',
        REMOTE_EMS: 'Allows advanced remote energy management control.',
        CUSTOM: 'Runs user-defined custom operating behavior.'
    };

    // Fetch config and pre-populate fields
    fetch('/config')
        .then(res => res.json())
        .then(cfg => {
            document.getElementById('inverter_kw').value = cfg.inverter_kw;
            document.getElementById('battery_kwh').value = cfg.battery_kwh;
            document.getElementById('solar_pv_kw').value = cfg.solar_pv_kw;
            if (!document.getElementById('soc').value) {
                document.getElementById('soc').value = 80;
            }
        });

    document.getElementById('sim-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const data = {
            inverter_kw: parseFloat(document.getElementById('inverter_kw').value),
            battery_kwh: parseFloat(document.getElementById('battery_kwh').value),
            solar_pv_kw: parseFloat(document.getElementById('solar_pv_kw').value),
            soc: parseFloat(document.getElementById('soc').value),
            forecast_morn: document.getElementById('forecast_morn').value,
            forecast_aftn: document.getElementById('forecast_aftn').value,
            forecast_eve: document.getElementById('forecast_eve').value
        };
        const res = await fetch('/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        renderResult(result);
    });

    function renderResult(result) {
        const resultDiv = document.getElementById('result');
        if (!result || typeof result !== 'object') {
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<b>No result returned.</b>';
            return;
        }
        let html = '<h3>Simulation Result</h3>';
        html += '<table class="mode-table">';
        html += '<tr><th>Period</th><th>Mode</th><th>Mode Explanation</th><th>Reason</th></tr>';
        for (const period of ['Morn', 'Aftn', 'Eve', 'NIGHT']) {
            if (result[period]) {
                const mode = result[period].mode_name;
                const modeLabel = MODE_LABELS[mode] || mode;
                const modeExplanation = MODE_EXPLANATIONS[mode] || 'No description available for this mode.';
                html += `<tr><td>${period}</td><td class="mode-${mode}">${modeLabel}</td><td>${modeExplanation}</td><td>${result[period].reason}</td></tr>`;
            }
        }
        html += '</table>';
        resultDiv.innerHTML = html;
        resultDiv.style.display = 'block';
    }
});
