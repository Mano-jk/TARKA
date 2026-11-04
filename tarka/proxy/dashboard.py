"""Static, dependency-free dashboard for the aggregate metrics endpoint."""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TARKA metrics</title>
    <style>
      body {
        font: 16px system-ui, sans-serif;
        margin: 2rem auto;
        max-width: 52rem;
        padding: 0 1rem;
      }
      label, input, button { display: block; margin: 0.6rem 0; }
      input { box-sizing: border-box; max-width: 32rem; padding: 0.5rem; width: 100%; }
      button { cursor: pointer; padding: 0.5rem 1rem; }
      pre { background: #f3f4f6; overflow: auto; padding: 1rem; }
      #error { color: #a11; }
    </style>
  </head>
  <body>
    <h1>TARKA gateway metrics</h1>
    <p>Process-local aggregates only. Prompt and plan contents are not collected.</p>
    <form id="metrics-form">
      <label for="api-key">Proxy API key</label>
      <input id="api-key" type="password" autocomplete="off" required>
      <button type="submit">Load metrics</button>
    </form>
    <p id="error" role="alert"></p>
    <pre id="metrics" aria-live="polite">Metrics have not been loaded.</pre>
    <script>
      const form = document.getElementById("metrics-form");
      const keyInput = document.getElementById("api-key");
      const output = document.getElementById("metrics");
      const error = document.getElementById("error");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.textContent = "";
        output.textContent = "Loading...";
        try {
          const response = await fetch("/dashboard/metrics", {
            headers: { Authorization: `Bearer ${keyInput.value}` },
            cache: "no-store",
          });
          const body = await response.json();
          if (!response.ok) {
            throw new Error(body.error?.message || `HTTP ${response.status}`);
          }
          output.textContent = JSON.stringify(body, null, 2);
        } catch (reason) {
          output.textContent = "Metrics unavailable.";
          error.textContent = reason instanceof Error ? reason.message : "Request failed.";
        }
      });
    </script>
  </body>
</html>
"""
