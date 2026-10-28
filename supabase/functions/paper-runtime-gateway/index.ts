import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SECRET_KEYS = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
const SECRET_KEY = SECRET_KEYS["default"] || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const EXPECTED_TOKEN_HASH = "bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933";

const ACTIONS: Record<string, string> = {
  snapshot: "triconsensus_dashboard_snapshot",
  open: "triconsensus_open_paper_trade",
  monitor: "triconsensus_monitor_paper_trade",
  close: "triconsensus_close_paper_trade",
};

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") {
    return Response.json({ ok: false, error: "method_not_allowed" }, { status: 405 });
  }

  try {
    const body = await request.json();
    const token = String(body?.token || "");
    const action = String(body?.action || "");
    if (!token || (await sha256Hex(token)) !== EXPECTED_TOKEN_HASH) {
      return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
    }
    const rpc = ACTIONS[action];
    if (!rpc) {
      return Response.json({ ok: false, error: "invalid_action" }, { status: 400 });
    }
    if (!SECRET_KEY) {
      throw new Error("supabase_secret_key_missing");
    }

    const rpcBody =
      action === "snapshot"
        ? { p_token: token }
        : { p_token: token, p_payload: body?.payload || {} };

    const response = await fetch(
      SUPABASE_URL + "/rest/v1/rpc/" + rpc,
      {
        method: "POST",
        headers: {
          apikey: SECRET_KEY,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(rpcBody),
      },
    );

    const responseText = await response.text();
    let data: unknown = responseText;
    try {
      data = responseText ? JSON.parse(responseText) : null;
    } catch {
      data = responseText.slice(0, 500);
    }

    if (!response.ok) {
      return Response.json(
        { ok: false, error: "rpc_failed", status: response.status, detail: data },
        { status: 502 },
      );
    }

    return Response.json({
      ok: true,
      action,
      paper_only: true,
      live_trading: false,
      data,
    });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        paper_only: true,
        live_trading: false,
      },
      { status: 500 },
    );
  }
});
