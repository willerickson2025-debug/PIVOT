import CompareForm from "./_components/CompareForm";

export default function ComparePage() {
  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px" }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, color: "var(--bone)", marginBottom: 24 }}>
        Compare
      </h1>
      <CompareForm />
    </div>
  );
}
