// Runs the engine from dashboard.html on CSV files and prints figures as JSON.
// Usage: node engine_runner.js <dashboard.html> <spec.json>
const fs = require("fs");
const [, , htmlPath, specPath] = process.argv;
const html = fs.readFileSync(htmlPath, "utf8");
const config = html.match(/const CONFIG = (\{[\s\S]*?\n\});/)[1];
const engine = html.split("// ENGINE START")[1].split("// ENGINE END")[0];
const { Engine, CONFIG } = new Function(`const CONFIG = ${config};\n${engine}\nreturn { Engine, CONFIG };`)();
const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
const files = spec.files.map((f) => ({ name: f.split("/").pop(), text: fs.readFileSync(f, "utf8") }));
const model = Engine.build(files, CONFIG);
const out = { health: { rowsAfterDedupe: model.health.rowsAfterDedupe, duplicatesRemoved: model.health.duplicatesRemoved,
  completedLeads: model.health.completedLeads, partialWithCommission: model.health.partialWithCommission,
  unmappedFormIds: model.health.unmappedFormIds.map((r) => r.value).sort(),
  unmappedFormTypes: model.health.unmappedFormTypes.map((r) => r.value).sort() }, cases: [] };
for (const c of spec.cases) {
  const sel = {};
  for (const [k, v] of Object.entries(c.selection)) sel[k] = new Set(v);
  const ok = Engine.startsAvailable(sel);
  const leads = Engine.applyFilters(c.followOns ? model.leadsWithFollowOns : model.leads, sel);
  const starts = Engine.applyFilters(model.starts, sel, true);
  const s = Engine.dayFromISO(c.start), e = Engine.dayFromISO(c.end);
  const summary = Engine.summarise(leads, starts, c.channel, s, e, ok);
  const table = Engine.breakdown(leads, starts, c.channel, s, e, c.gran, ok)
    .map((r) => ({ label: r.label, isPart: r.isPart, blended_revenue: r.blended_revenue, blended_enquiries: r.blended_enquiries, form_starts: r.form_starts }));
  const routes = Engine.routesInData(leads).map((r) => ({ ...r, summary: Engine.routeSummary(leads, r, s, e) }));
  const bridge = Engine.channelBridge(leads, c.channel, s, e, c.gran);
  const phil = Engine.philsView(leads, "Leased Lines", "Broadband", s, e);
  const reconcile = Engine.philsReconcile(leads, "Leased Lines", "Broadband", s, e);
  out.cases.push({ summary, table, routes, phil, reconcile, bridge: bridge.total,
    bridgeRowsRevenue: bridge.rows.reduce((t, r) => t + r.blended_revenue, 0) });
}
out.checks = Engine.acceptanceChecks(model).length;
process.stdout.write(JSON.stringify(out));
