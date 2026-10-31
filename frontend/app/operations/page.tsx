import {getOperationsSnapshot} from "@/lib/operations";
import OperationsClient from "./OperationsClient";

export const dynamic = "force-dynamic";

export default async function OperationsPage() {
  const initial = await getOperationsSnapshot(120);
  return <OperationsClient initial={initial} />;
}
