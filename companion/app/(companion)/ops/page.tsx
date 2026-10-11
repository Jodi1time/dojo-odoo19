import TodayView from "@/components/workspace/TodayView";
export const dynamic = "force-dynamic";
export default function OpsPage() {
  return (
    <TodayView
      classesOnly
      demo={process.env.NEXT_PUBLIC_DEMO_MODE === "true"}
    />
  );
}
