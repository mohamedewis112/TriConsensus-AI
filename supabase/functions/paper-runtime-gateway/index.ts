import "jsr:@supabase/functions-js/edge-runtime.d.ts";

Deno.serve(() =>
  Response.json(
    {
      ok: false,
      error: "deprecated_runtime_gateway_disabled",
      paper_only: true,
      live_trading: false,
    },
    { status: 410 },
  )
);
