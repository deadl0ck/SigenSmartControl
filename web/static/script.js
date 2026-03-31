document.addEventListener('DOMContentLoaded', () => {
    // Fetch config and pre-populate fields
    fetch('/config')
        .then(res => res.json())
        .then(cfg => {
            document.getElementById('inverter_kw').value = cfg.inverter_kw;
            document.getElementById('battery_kwh').value = cfg.battery_kwh;
            document.getElementById('solar_pv_kw').value = cfg.solar_pv_kw;
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
        html += '<tr><th>Period</th><th>Mode</th><th>Reason</th></tr>';
        for (const period of ['Morn', 'Aftn', 'Eve']) {
            if (result[period]) {
                const mode = result[period].mode_name;
                html += `<tr><td>${period}</td><td class="mode-${mode}">${mode}</td><td>${result[period].reason}</td></tr>`;
            }
        }
        html += '</table>';
        resultDiv.innerHTML = html;
        resultDiv.style.display = 'block';
    }
});
