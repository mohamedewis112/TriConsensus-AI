import {NextRequest, NextResponse} from "next/server";
import {getOperationsSnapshot} from "@/lib/operations";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const raw = Number(request.nextUrl.searchParams.get("limit") ?? "100");
  const limit = Number.isFinite(raw) ? Math.max(10, Math.min(Math.trunc(raw), 200)) : 100;
  const snapshot = await getOperationsSnapshot(limit);

  if (!snapshot) {
    return NextResponse.json(
      {error: "operations backend unavailable", default_action: "NO_TRADE"},
      {status: 503, headers: {"Cache-Control": "no-store"}},
    );
  }

  return NextResponse.json(snapshot, {
    status: 200,
    headers: {"Cache-Control": "no-store, max-age=0"},
  });
}
