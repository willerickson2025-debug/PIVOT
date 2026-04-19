import PlayerSearch from "./_components/PlayerSearch";

export default function PlayersPage() {
  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px" }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, color: "var(--bone)", marginBottom: 24 }}>
        Players
      </h1>
      <PlayerSearch />
    </div>
  );
}
