// ---------------------------------------------------------------------------
// Brand tokens
// ---------------------------------------------------------------------------

export const SIG = "#39FF14";
export const CORAL = "#FF6B4A";

// ---------------------------------------------------------------------------
// CBA 2025-26 Constants (all in millions)
// ---------------------------------------------------------------------------

export const CBA = {
  CAP: 141.0,
  LUXURY_TAX: 170.8,
  APRON_1: 178.1,
  APRON_2: 188.9,
  NON_TAX_MLE: 14.1,
  TAX_MLE: 5.1,
  BAE: 4.5,
  MINIMUM_SALARY: 1.19,
  SEASON: "2025-26",
} as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CapTier = "under" | "cap" | "tax" | "apron1" | "apron2" | "over2";

export interface RailTeam {
  abbr: string;
  city: string;
  name: string;
  conf: "East" | "West";
  div: string;
  payroll: number;
  tier: CapTier;
  wins?: number;
  losses?: number;
  gm?: string;
  coach?: string;
  owner?: string;
}

export interface RosterPlayer {
  name: string;
  pos: string;
  salary: number;
  years: number;
  extension?: boolean;
  team_option?: boolean;
  player_option?: boolean;
  two_way?: boolean;
  notes?: string;
}

export interface FranchiseData {
  abbr: string;
  city: string;
  name: string;
  conf: "East" | "West";
  div: string;
  wins: number;
  losses: number;
  payroll: number;
  tier: CapTier;
  gm: string;
  coach: string;
  owner: string;
  arena: string;
  founded: number;
  championships: number;
  capsummary: string;
  roster: RosterPlayer[];
}

// ---------------------------------------------------------------------------
// Tier maps
// ---------------------------------------------------------------------------

export const TIER_LABEL: Record<CapTier, string> = {
  under: "UNDER CAP",
  cap: "CAP SPACE",
  tax: "OVER CAP",
  apron1: "LUXURY TAX",
  apron2: "APRON 1",
  over2: "APRON 2",
};

export const TIER_COLOR: Record<CapTier, string> = {
  under: SIG,
  cap: "#7EE8A2",
  tax: "#F9CA24",
  apron1: CORAL,
  apron2: "#FF4444",
  over2: "#CC0000",
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

export function fmtM(n: number): string {
  return `$${n.toFixed(1)}M`;
}

export function fmtDelta(n: number): string {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}M`;
}

// ---------------------------------------------------------------------------
// RAIL_DATA - all 30 teams
// ---------------------------------------------------------------------------

export const RAIL_DATA: RailTeam[] = [
  // Atlantic
  { abbr: "BOS", city: "Boston", name: "Celtics", conf: "East", div: "Atlantic", payroll: 194.2, tier: "over2", wins: 52, losses: 22, gm: "Brad Stevens", coach: "Joe Mazzulla" },
  { abbr: "BKN", city: "Brooklyn", name: "Nets", conf: "East", div: "Atlantic", payroll: 118.4, tier: "under", wins: 26, losses: 48, gm: "Sean Marks", coach: "Jordi Fernandez" },
  { abbr: "NYK", city: "New York", name: "Knicks", conf: "East", div: "Atlantic", payroll: 183.1, tier: "apron2", wins: 50, losses: 24, gm: "Scott Perry", coach: "Tom Thibodeau" },
  { abbr: "PHI", city: "Philadelphia", name: "76ers", conf: "East", div: "Atlantic", payroll: 162.4, tier: "apron1", wins: 38, losses: 36, gm: "Daryl Morey", coach: "Nick Nurse" },
  { abbr: "TOR", city: "Toronto", name: "Raptors", conf: "East", div: "Atlantic", payroll: 124.7, tier: "under", wins: 30, losses: 44, gm: "Masai Ujiri", coach: "Darko Rajakovic" },
  // Central
  { abbr: "CHI", city: "Chicago", name: "Bulls", conf: "East", div: "Central", payroll: 143.8, tier: "tax", wins: 35, losses: 39, gm: "Marc Eversley", coach: "Billy Donovan" },
  { abbr: "CLE", city: "Cleveland", name: "Cavaliers", conf: "East", div: "Central", payroll: 176.3, tier: "apron2", wins: 58, losses: 16, gm: "Koby Altman", coach: "Kenny Atkinson" },
  { abbr: "DET", city: "Detroit", name: "Pistons", conf: "East", div: "Central", payroll: 131.2, tier: "under", wins: 28, losses: 46, gm: "Troy Weaver", coach: "J.B. Bickerstaff" },
  { abbr: "IND", city: "Indiana", name: "Pacers", conf: "East", div: "Central", payroll: 148.6, tier: "apron1", wins: 44, losses: 30, gm: "Chad Buchanan", coach: "Rick Carlisle" },
  { abbr: "MIL", city: "Milwaukee", name: "Bucks", conf: "East", div: "Central", payroll: 168.9, tier: "apron1", wins: 40, losses: 34, gm: "Jon Horst", coach: "Doc Rivers" },
  // Southeast
  { abbr: "ATL", city: "Atlanta", name: "Hawks", conf: "East", div: "Southeast", payroll: 154.1, tier: "apron1", wins: 36, losses: 38, gm: "Landry Fields", coach: "Quin Snyder" },
  { abbr: "CHA", city: "Charlotte", name: "Hornets", conf: "East", div: "Southeast", payroll: 119.6, tier: "under", wins: 22, losses: 52, gm: "Jeff Peterson", coach: "Charles Lee" },
  { abbr: "MIA", city: "Miami", name: "Heat", conf: "East", div: "Southeast", payroll: 161.7, tier: "apron1", wins: 42, losses: 32, gm: "Pat Riley", coach: "Erik Spoelstra" },
  { abbr: "ORL", city: "Orlando", name: "Magic", conf: "East", div: "Southeast", payroll: 136.4, tier: "under", wins: 46, losses: 28, gm: "Jeff Weltman", coach: "Jamahl Mosley" },
  { abbr: "WAS", city: "Washington", name: "Wizards", conf: "East", div: "Southeast", payroll: 112.3, tier: "under", wins: 19, losses: 55, gm: "Will Dawkins", coach: "Brian Keefe" },
  // Northwest
  { abbr: "DEN", city: "Denver", name: "Nuggets", conf: "West", div: "Northwest", payroll: 188.4, tier: "apron2", wins: 50, losses: 24, gm: "Calvin Booth", coach: "Michael Malone" },
  { abbr: "MIN", city: "Minnesota", name: "Timberwolves", conf: "West", div: "Northwest", payroll: 181.6, tier: "apron2", wins: 49, losses: 25, gm: "Tim Connelly", coach: "Chris Finch" },
  { abbr: "OKC", city: "Oklahoma City", name: "Thunder", conf: "West", div: "Northwest", payroll: 152.8, tier: "apron1", wins: 63, losses: 11, gm: "Sam Presti", coach: "Mark Daigneault" },
  { abbr: "POR", city: "Portland", name: "Trail Blazers", conf: "West", div: "Northwest", payroll: 116.2, tier: "under", wins: 24, losses: 50, gm: "Joe Cronin", coach: "Chauncey Billups" },
  { abbr: "UTA", city: "Utah", name: "Jazz", conf: "West", div: "Northwest", payroll: 108.9, tier: "under", wins: 21, losses: 53, gm: "Danny Ainge", coach: "Will Hardy" },
  // Pacific
  { abbr: "GSW", city: "Golden State", name: "Warriors", conf: "West", div: "Pacific", payroll: 177.4, tier: "apron2", wins: 44, losses: 30, gm: "Mike Dunleavy Jr.", coach: "Steve Kerr" },
  { abbr: "LAC", city: "Los Angeles", name: "Clippers", conf: "West", div: "Pacific", payroll: 169.3, tier: "apron1", wins: 39, losses: 35, gm: "Lawrence Frank", coach: "Tyronn Lue" },
  { abbr: "LAL", city: "Los Angeles", name: "Lakers", conf: "West", div: "Pacific", payroll: 172.6, tier: "apron2", wins: 45, losses: 29, gm: "Rob Pelinka", coach: "JJ Redick" },
  { abbr: "PHX", city: "Phoenix", name: "Suns", conf: "West", div: "Pacific", payroll: 184.7, tier: "apron2", wins: 36, losses: 38, gm: "James Jones", coach: "Mike Budenholzer" },
  { abbr: "SAC", city: "Sacramento", name: "Kings", conf: "West", div: "Pacific", payroll: 158.3, tier: "apron1", wins: 41, losses: 33, gm: "Monte McNair", coach: "Doug Christie" },
  // Southwest
  { abbr: "DAL", city: "Dallas", name: "Mavericks", conf: "West", div: "Southwest", payroll: 174.1, tier: "apron2", wins: 46, losses: 28, gm: "Nico Harrison", coach: "Jason Kidd" },
  { abbr: "HOU", city: "Houston", name: "Rockets", conf: "West", div: "Southwest", payroll: 139.4, tier: "under", wins: 51, losses: 23, gm: "Rafael Stone", coach: "Ime Udoka" },
  { abbr: "MEM", city: "Memphis", name: "Grizzlies", conf: "West", div: "Southwest", payroll: 144.2, tier: "tax", wins: 37, losses: 37, gm: "Zach Kleiman", coach: "Taylor Jenkins" },
  { abbr: "NOP", city: "New Orleans", name: "Pelicans", conf: "West", div: "Southwest", payroll: 122.8, tier: "under", wins: 22, losses: 52, gm: "David Griffin", coach: "Willie Green" },
  { abbr: "SAS", city: "San Antonio", name: "Spurs", conf: "West", div: "Southwest", payroll: 134.6, tier: "under", wins: 25, losses: 49, gm: "Brian Wright", coach: "Gregg Popovich" },
];

// ---------------------------------------------------------------------------
// FRANCHISE_STUBS - roster data for all 30 teams
// ---------------------------------------------------------------------------

export const FRANCHISE_STUBS: Record<string, FranchiseData> = {
  OKC: {
    abbr: "OKC", city: "Oklahoma City", name: "Thunder", conf: "West", div: "Northwest",
    wins: 63, losses: 11, payroll: 152.8, tier: "apron1",
    gm: "Sam Presti", coach: "Mark Daigneault", owner: "Clay Bennett",
    arena: "Paycom Center", founded: 2008, championships: 0,
    capsummary: "OKC controls the league's deepest asset base with SGA locked through 2030. Presti built the youngest title contender in the league without compromising future flexibility.",
    roster: [
      { name: "Shai Gilgeous-Alexander", pos: "G", salary: 34.0, years: 4 },
      { name: "Jalen Williams", pos: "F", salary: 26.5, years: 4 },
      { name: "Isaiah Hartenstein", pos: "C", salary: 16.0, years: 4 },
      { name: "Alex Caruso", pos: "G", salary: 13.1, years: 2 },
      { name: "Lu Dort", pos: "G", salary: 15.2, years: 2 },
      { name: "Chet Holmgren", pos: "C", salary: 10.2, years: 2 },
      { name: "Aaron Wiggins", pos: "G", salary: 7.8, years: 2 },
      { name: "Kenrich Williams", pos: "F", salary: 8.2, years: 1 },
      { name: "Josh Giddey", pos: "G", salary: 8.7, years: 1, player_option: true },
      { name: "Jaylin Williams", pos: "F", salary: 3.1, years: 2 },
    ],
  },
  BOS: {
    abbr: "BOS", city: "Boston", name: "Celtics", conf: "East", div: "Atlantic",
    wins: 52, losses: 22, payroll: 194.2, tier: "over2",
    gm: "Brad Stevens", coach: "Joe Mazzulla", owner: "Wyc Grousbeck",
    arena: "TD Garden", founded: 1946, championships: 18,
    capsummary: "Boston is the deepest over-second-apron team in the league, locked into their core through at least 2027. Stevens has accepted the luxury-tax bill as the cost of a championship-caliber roster.",
    roster: [
      { name: "Jayson Tatum", pos: "F", salary: 37.6, years: 5 },
      { name: "Jaylen Brown", pos: "G/F", salary: 34.0, years: 5 },
      { name: "Kristaps Porzingis", pos: "C", salary: 30.7, years: 3 },
      { name: "Jrue Holiday", pos: "G", salary: 28.0, years: 2 },
      { name: "Derrick White", pos: "G", salary: 22.6, years: 4 },
      { name: "Al Horford", pos: "C", salary: 16.0, years: 1 },
      { name: "Payton Pritchard", pos: "G", salary: 14.8, years: 4 },
      { name: "Sam Hauser", pos: "F", salary: 9.5, years: 2 },
      { name: "Xavier Tillman", pos: "F", salary: 4.2, years: 1 },
      { name: "Jordan Crawford", pos: "G", salary: 2.1, years: 1 },
    ],
  },
  LAL: {
    abbr: "LAL", city: "Los Angeles", name: "Lakers", conf: "West", div: "Pacific",
    wins: 45, losses: 29, payroll: 172.6, tier: "apron2",
    gm: "Rob Pelinka", coach: "JJ Redick", owner: "Jeanie Buss",
    arena: "Crypto.com Arena", founded: 1947, championships: 17,
    capsummary: "LA locked LeBron and AD through 2026 but faces hard apron constraints with limited roster depth. Pelinka has minimal trade ammunition with most picks mortgaged. The window is short.",
    roster: [
      { name: "LeBron James", pos: "F", salary: 51.4, years: 1 },
      { name: "Anthony Davis", pos: "C", salary: 43.2, years: 2 },
      { name: "Austin Reaves", pos: "G", salary: 13.8, years: 3 },
      { name: "D'Angelo Russell", pos: "G", salary: 18.0, years: 1 },
      { name: "Rui Hachimura", pos: "F", salary: 17.2, years: 3 },
      { name: "Gabe Vincent", pos: "G", salary: 11.0, years: 1 },
      { name: "Taurean Prince", pos: "F", salary: 4.8, years: 1 },
      { name: "Christian Wood", pos: "C", salary: 3.5, years: 1 },
      { name: "Cam Reddish", pos: "F", salary: 4.0, years: 1 },
      { name: "Spencer Dinwiddie", pos: "G", salary: 4.7, years: 1 },
    ],
  },
  GSW: {
    abbr: "GSW", city: "Golden State", name: "Warriors", conf: "West", div: "Pacific",
    wins: 44, losses: 30, payroll: 177.4, tier: "apron2",
    gm: "Mike Dunleavy Jr.", coach: "Steve Kerr", owner: "Joe Lacob",
    arena: "Chase Center", founded: 1946, championships: 7,
    capsummary: "Golden State is paying dynasty-era contracts into a transition period. Steph is still elite but the surrounding cast has eroded. Dunleavy faces a core rebuild while staying competitive enough to hold the dynasty's legacy.",
    roster: [
      { name: "Stephen Curry", pos: "G", salary: 55.8, years: 2 },
      { name: "Draymond Green", pos: "F", salary: 22.3, years: 1 },
      { name: "Klay Thompson", pos: "G", salary: 43.2, years: 1 },
      { name: "Andrew Wiggins", pos: "F", salary: 24.3, years: 1 },
      { name: "Jonathan Kuminga", pos: "F", salary: 7.5, years: 1 },
      { name: "Moses Moody", pos: "G", salary: 4.4, years: 2 },
      { name: "Gary Payton II", pos: "G", salary: 7.4, years: 1 },
      { name: "Kevon Looney", pos: "C", salary: 7.5, years: 1 },
      { name: "Brandin Podziemski", pos: "G", salary: 3.8, years: 2 },
      { name: "Trayce Jackson-Davis", pos: "C", salary: 1.9, years: 2 },
    ],
  },
  DEN: {
    abbr: "DEN", city: "Denver", name: "Nuggets", conf: "West", div: "Northwest",
    wins: 50, losses: 24, payroll: 188.4, tier: "apron2",
    gm: "Calvin Booth", coach: "Michael Malone", owner: "Stan Kroenke",
    arena: "Ball Arena", founded: 1967, championships: 1,
    capsummary: "Denver's Jokic-era window is full open but they're deep into the second apron, limiting flexibility. Booth has largely preserved the core while paying market rate for the best player in the world.",
    roster: [
      { name: "Nikola Jokic", pos: "C", salary: 51.4, years: 3 },
      { name: "Jamal Murray", pos: "G", salary: 38.6, years: 3 },
      { name: "Michael Porter Jr.", pos: "F", salary: 33.8, years: 3 },
      { name: "Aaron Gordon", pos: "F", salary: 21.0, years: 2 },
      { name: "Kentavious Caldwell-Pope", pos: "G", salary: 14.5, years: 1 },
      { name: "Reggie Jackson", pos: "G", salary: 4.2, years: 1 },
      { name: "Christian Braun", pos: "G/F", salary: 4.1, years: 3 },
      { name: "Vlatko Cancar", pos: "F", salary: 8.6, years: 2 },
      { name: "Peyton Watson", pos: "F", salary: 3.3, years: 3 },
      { name: "DeAndre Jordan", pos: "C", salary: 3.0, years: 1 },
    ],
  },
  NYK: {
    abbr: "NYK", city: "New York", name: "Knicks", conf: "East", div: "Atlantic",
    wins: 50, losses: 24, payroll: 183.1, tier: "apron2",
    gm: "Scott Perry", coach: "Tom Thibodeau", owner: "James Dolan",
    arena: "Madison Square Garden", founded: 1946, championships: 2,
    capsummary: "New York is over the second apron after committing to Brunson and adding Mikal Bridges in a blockbuster trade. Perry is locked into this core for at least 3 more years with limited exit options.",
    roster: [
      { name: "Jalen Brunson", pos: "G", salary: 37.0, years: 4 },
      { name: "Julius Randle", pos: "F", salary: 28.9, years: 2 },
      { name: "Mikal Bridges", pos: "F", salary: 24.3, years: 4 },
      { name: "OG Anunoby", pos: "F", salary: 37.8, years: 4 },
      { name: "Isaiah Hartenstein", pos: "C", salary: 16.0, years: 4, notes: "signed away from OKC" },
      { name: "Josh Hart", pos: "G/F", salary: 19.5, years: 3 },
      { name: "Donte DiVincenzo", pos: "G", salary: 9.8, years: 1 },
      { name: "Precious Achiuwa", pos: "F", salary: 5.8, years: 1 },
      { name: "Miles McBride", pos: "G", salary: 4.0, years: 2 },
      { name: "Mitchell Robinson", pos: "C", salary: 15.4, years: 1 },
    ],
  },
  CLE: {
    abbr: "CLE", city: "Cleveland", name: "Cavaliers", conf: "East", div: "Central",
    wins: 58, losses: 16, payroll: 176.3, tier: "apron2",
    gm: "Koby Altman", coach: "Kenny Atkinson", owner: "Dan Gilbert",
    arena: "Rocket Mortgage FieldHouse", founded: 1970, championships: 1,
    capsummary: "Cleveland quietly built the East's best record by developing Mobley and Mitchell into a lethal two-man game. Altman locked up the core and is now paying for it with apron penalties but no regrets.",
    roster: [
      { name: "Donovan Mitchell", pos: "G", salary: 35.9, years: 4 },
      { name: "Evan Mobley", pos: "C/F", salary: 32.6, years: 4 },
      { name: "Darius Garland", pos: "G", salary: 36.0, years: 3 },
      { name: "Jarrett Allen", pos: "C", salary: 20.0, years: 2 },
      { name: "Max Strus", pos: "G/F", salary: 13.8, years: 2 },
      { name: "Caris LeVert", pos: "G", salary: 11.8, years: 1 },
      { name: "Georges Niang", pos: "F", salary: 7.4, years: 1 },
      { name: "Craig Porter Jr.", pos: "G", salary: 2.0, years: 2 },
      { name: "Isaac Okoro", pos: "F", salary: 9.0, years: 2 },
      { name: "Dean Wade", pos: "F", salary: 4.6, years: 1 },
    ],
  },
  MIL: {
    abbr: "MIL", city: "Milwaukee", name: "Bucks", conf: "East", div: "Central",
    wins: 40, losses: 34, payroll: 168.9, tier: "apron1",
    gm: "Jon Horst", coach: "Doc Rivers", owner: "Marc Lasry",
    arena: "Fiserv Forum", founded: 1968, championships: 2,
    capsummary: "Milwaukee is navigating the post-Budenholzer reset with Giannis still at peak but Khris Middleton's health a persistent question. Horst has the tax flexibility to add but limited trade assets after years of win-now moves.",
    roster: [
      { name: "Giannis Antetokounmpo", pos: "F/C", salary: 48.8, years: 3 },
      { name: "Damian Lillard", pos: "G", salary: 48.8, years: 3 },
      { name: "Khris Middleton", pos: "F", salary: 33.4, years: 1 },
      { name: "Brook Lopez", pos: "C", salary: 15.1, years: 1 },
      { name: "Bobby Portis", pos: "F", salary: 12.6, years: 1 },
      { name: "Patrick Beverley", pos: "G", salary: 3.5, years: 1 },
      { name: "MarJon Beauchamp", pos: "F", salary: 3.2, years: 2 },
      { name: "AJ Green", pos: "G", salary: 2.8, years: 2 },
      { name: "Malik Beasley", pos: "G", salary: 8.0, years: 1 },
      { name: "Robin Lopez", pos: "C", salary: 3.5, years: 1 },
    ],
  },
  DAL: {
    abbr: "DAL", city: "Dallas", name: "Mavericks", conf: "West", div: "Southwest",
    wins: 46, losses: 28, payroll: 174.1, tier: "apron2",
    gm: "Nico Harrison", coach: "Jason Kidd", owner: "Mark Cuban",
    arena: "American Airlines Center", founded: 1980, championships: 1,
    capsummary: "Dallas is all-in on the Luka-Kyrie pairing and paying for it above the second apron. Harrison has stripped most of the surrounding depth to maintain the superstar duo. Window is open but narrow.",
    roster: [
      { name: "Luka Doncic", pos: "G/F", salary: 43.0, years: 4 },
      { name: "Kyrie Irving", pos: "G", salary: 37.0, years: 2 },
      { name: "Tim Hardaway Jr.", pos: "G", salary: 18.0, years: 1 },
      { name: "Dereck Lively II", pos: "C", salary: 5.8, years: 3 },
      { name: "P.J. Washington", pos: "F", salary: 15.5, years: 3 },
      { name: "Dante Exum", pos: "G", salary: 8.8, years: 1 },
      { name: "Grant Williams", pos: "F", salary: 14.0, years: 2 },
      { name: "Seth Curry", pos: "G", salary: 8.5, years: 1 },
      { name: "Maxi Kleber", pos: "F", salary: 10.2, years: 1 },
      { name: "Dwight Powell", pos: "C", salary: 5.9, years: 1 },
    ],
  },
  HOU: {
    abbr: "HOU", city: "Houston", name: "Rockets", conf: "West", div: "Southwest",
    wins: 51, losses: 23, payroll: 139.4, tier: "under",
    gm: "Rafael Stone", coach: "Ime Udoka", owner: "Tilman Fertitta",
    arena: "Toyota Center", founded: 1967, championships: 2,
    capsummary: "Houston is the league's best cap-space story: 51 wins and under the salary cap. Stone developed Jalen Green and Alperen Sengun organically and still has room to add a third star before this core gets expensive.",
    roster: [
      { name: "Jalen Green", pos: "G", salary: 32.6, years: 4 },
      { name: "Alperen Sengun", pos: "C", salary: 28.0, years: 4 },
      { name: "Dillon Brooks", pos: "F", salary: 22.0, years: 2 },
      { name: "Fred VanVleet", pos: "G", salary: 44.0, years: 2 },
      { name: "Jabari Smith Jr.", pos: "F", salary: 9.0, years: 3 },
      { name: "Amen Thompson", pos: "G/F", salary: 6.1, years: 3 },
      { name: "Cam Whitmore", pos: "F", salary: 3.8, years: 3 },
      { name: "Aaron Holiday", pos: "G", salary: 3.3, years: 1 },
      { name: "Tari Eason", pos: "F", salary: 4.4, years: 2 },
      { name: "Jeff Green", pos: "F", salary: 3.2, years: 1 },
    ],
  },
  MIN: {
    abbr: "MIN", city: "Minnesota", name: "Timberwolves", conf: "West", div: "Northwest",
    wins: 49, losses: 25, payroll: 181.6, tier: "apron2",
    gm: "Tim Connelly", coach: "Chris Finch", owner: "Glen Taylor",
    arena: "Target Center", founded: 1989, championships: 0,
    capsummary: "Minnesota committed fully to the Ant-Gobert pairing and added KAT before trading him. Now over the second apron, Connelly faces hard roster decisions with the core locked but no cheap depth available.",
    roster: [
      { name: "Anthony Edwards", pos: "G/F", salary: 41.2, years: 4 },
      { name: "Karl-Anthony Towns", pos: "C", salary: 54.0, years: 3, notes: "returned via trade" },
      { name: "Rudy Gobert", pos: "C", salary: 43.8, years: 2 },
      { name: "Mike Conley", pos: "G", salary: 10.5, years: 1 },
      { name: "Jaden McDaniels", pos: "F", salary: 15.6, years: 3 },
      { name: "Naz Reid", pos: "F/C", salary: 14.0, years: 3 },
      { name: "Kyle Anderson", pos: "F", salary: 8.0, years: 1 },
      { name: "Nickeil Alexander-Walker", pos: "G", salary: 10.2, years: 2 },
      { name: "Monte Morris", pos: "G", salary: 4.3, years: 1 },
      { name: "Leonard Miller", pos: "F", salary: 2.2, years: 3 },
    ],
  },
  PHX: {
    abbr: "PHX", city: "Phoenix", name: "Suns", conf: "West", div: "Pacific",
    wins: 36, losses: 38, payroll: 184.7, tier: "apron2",
    gm: "James Jones", coach: "Mike Budenholzer", owner: "Mat Ishbia",
    arena: "Footprint Center", founded: 1968, championships: 0,
    capsummary: "Phoenix traded their future for a superteam that underperformed. Ishbia's aggressive moves left the franchise over the second apron with an aging core and almost no draft capital. Jones must navigate a difficult rebuild while remaining competitive.",
    roster: [
      { name: "Kevin Durant", pos: "F", salary: 51.2, years: 1 },
      { name: "Bradley Beal", pos: "G", salary: 46.7, years: 2 },
      { name: "Devin Booker", pos: "G", salary: 38.5, years: 3 },
      { name: "Jusuf Nurkic", pos: "C", salary: 18.0, years: 1 },
      { name: "Grayson Allen", pos: "G", salary: 10.8, years: 1 },
      { name: "Eric Gordon", pos: "G", salary: 8.0, years: 1 },
      { name: "Royce O'Neale", pos: "F", salary: 9.4, years: 1 },
      { name: "Bol Bol", pos: "C", salary: 4.3, years: 1 },
      { name: "Nassir Little", pos: "F", salary: 3.8, years: 1 },
      { name: "Keita Bates-Diop", pos: "F", salary: 3.0, years: 1 },
    ],
  },
  MIA: {
    abbr: "MIA", city: "Miami", name: "Heat", conf: "East", div: "Southeast",
    wins: 42, losses: 32, payroll: 161.7, tier: "apron1",
    gm: "Pat Riley", coach: "Erik Spoelstra", owner: "Micky Arison",
    arena: "Kaseya Center", founded: 1988, championships: 3,
    capsummary: "Miami's culture system keeps producing relevant seasons without a true superstar. Riley has Butler locked but continues to leverage the Heat's brand to reload through trades and undrafted finds.",
    roster: [
      { name: "Jimmy Butler", pos: "F", salary: 48.8, years: 2 },
      { name: "Bam Adebayo", pos: "C", salary: 32.6, years: 3 },
      { name: "Tyler Herro", pos: "G", salary: 29.3, years: 3 },
      { name: "Terry Rozier", pos: "G", salary: 26.5, years: 3 },
      { name: "Josh Richardson", pos: "G", salary: 7.0, years: 1 },
      { name: "Nikola Jovic", pos: "F", salary: 4.3, years: 2 },
      { name: "Kevin Love", pos: "F", salary: 4.0, years: 1 },
      { name: "Haywood Highsmith", pos: "F", salary: 3.8, years: 2 },
      { name: "Caleb Martin", pos: "F", salary: 9.0, years: 2 },
      { name: "Duncan Robinson", pos: "G/F", salary: 17.9, years: 1 },
    ],
  },
};

// Stub generator for teams without full roster data
export function getFranchise(abbr: string): FranchiseData {
  if (FRANCHISE_STUBS[abbr]) return FRANCHISE_STUBS[abbr];
  const rail = RAIL_DATA.find((t) => t.abbr === abbr);
  if (!rail) throw new Error(`Unknown team abbr: ${abbr}`);
  return {
    abbr: rail.abbr,
    city: rail.city,
    name: rail.name,
    conf: rail.conf,
    div: rail.div,
    wins: rail.wins ?? 0,
    losses: rail.losses ?? 0,
    payroll: rail.payroll,
    tier: rail.tier,
    gm: rail.gm ?? "TBD",
    coach: rail.coach ?? "TBD",
    owner: "",
    arena: "",
    founded: 0,
    championships: 0,
    capsummary: "Full franchise data coming soon.",
    roster: [],
  };
}

// ---------------------------------------------------------------------------
// Deadline countdown (base date: April 19, 2026)
// ---------------------------------------------------------------------------

export interface Deadline {
  label: string;
  sublabel: string;
  date: Date;
  daysFromNow: number;
}

const NOW = new Date("2026-04-19T00:00:00");

function daysTo(target: Date): number {
  return Math.ceil((target.getTime() - NOW.getTime()) / (1000 * 60 * 60 * 24));
}

export const DEADLINES: Deadline[] = [
  { label: "NBA DRAFT", sublabel: "June 26, 2026", date: new Date("2026-06-26"), daysFromNow: daysTo(new Date("2026-06-26")) },
  { label: "QUALIFYING OFFERS", sublabel: "June 29, 2026", date: new Date("2026-06-29"), daysFromNow: daysTo(new Date("2026-06-29")) },
  { label: "FREE AGENCY", sublabel: "July 1, 2026", date: new Date("2026-07-01"), daysFromNow: daysTo(new Date("2026-07-01")) },
  { label: "SALARY CAP SET", sublabel: "July 1, 2026", date: new Date("2026-07-01"), daysFromNow: daysTo(new Date("2026-07-01")) },
  { label: "TRAINING CAMP", sublabel: "September 26, 2026", date: new Date("2026-09-26"), daysFromNow: daysTo(new Date("2026-09-26")) },
  { label: "TRADE DEADLINE", sublabel: "February 5, 2027", date: new Date("2027-02-05"), daysFromNow: daysTo(new Date("2027-02-05")) },
];

DEADLINES.sort((a, b) => a.daysFromNow - b.daysFromNow);
