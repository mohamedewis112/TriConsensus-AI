import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createRemoteJWKSet, jwtVerify } from "npm:jose@6.1.0";

const ISSUER = "https://oidc.vercel.com/smart-media1";
const AUDIENCE = "https://vercel.com/smart-media1";
const SUBJECT =
  "owner:smart-media1:project:triconsensus-runtime-preview:environment:preview";
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/.well-known/jwks`));
const INTERNAL_RPC_TOKEN = "internal-service-role";

async function authorize(req: Request): Promise<boolean> {
  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token) return false;
  try {
    await jwtVerify(token, JWKS, {
      issuer: ISSUER,
      audience: AUDIENCE,
      subject: SUBJECT,
    });
    return true;
  } catch (error) {
    console.error("vercel_oidc_rejected", {
      name: error instanceof Error ? error.name : "unknown",
    });
    return false;
  }
}

function adminClient() {
  const url = Deno.env.get("SUPABASE_URL");
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !serviceRole) throw new Error("supabase_service_role_missing");
  return createClient(url, serviceRole, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return Response.json({ error: "method_not_allowed" }, { status: 405 });
  }
  if (!(await authorize(req))) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: { action?: string; payload?: unknown };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }

  const admin = adminClient();
  let rpcName: string;
  let args: Record<string, unknown>;

  if (body.action === "snapshot") {
    rpcName = "triconsensus_runtime_snapshot";
    args = { p_token: INTERNAL_RPC_TOKEN };
  } else if (body.action === "commit") {
    if (!body.payload || typeof body.payload !== "object") {
      return Response.json({ error: "payload_required" }, { status: 400 });
    }
    rpcName = "triconsensus_runtime_commit_decision";
    args = { p_token: INTERNAL_RPC_TOKEN, p_payload: body.payload };
  } else if (body.action === "monitor") {
    if (!body.payload || typeof body.payload !== "object") {
      return Response.json({ error: "payload_required" }, { status: 400 });
    }
    rpcName = "triconsensus_runtime_monitor_paper_trade";
    args = { p_token: INTERNAL_RPC_TOKEN, p_payload: body.payload };
  } else {
    return Response.json({ error: "invalid_action" }, { status: 400 });
  }

  const { data, error } = await admin.rpc(rpcName, args);
  if (error) {
    console.error("paper_runtime_rpc_failed", {
      action: body.action,
      code: error.code,
      message: error.message,
    });
    return Response.json(
      { error: "runtime_rpc_failed", code: error.code || null },
      { status: 503 },
    );
  }

  return Response.json({
    ok: true,
    action: body.action,
    paper_only: true,
    live_trading: false,
    result: data,
  });
});
