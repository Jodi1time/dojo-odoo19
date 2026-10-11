import TodayView from "@/components/workspace/TodayView";
export default function Home() {
  return <TodayView demo={process.env.NEXT_PUBLIC_DEMO_MODE === "true"} />;
}
