// Run with Metrics UI's tsx loader; regenerate only when deliberately updating the translation baseline.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL, fileURLToPath } from 'node:url';
const root = path.resolve(process.argv[2]);
const commit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim();
const here = path.dirname(fileURLToPath(import.meta.url));
const load = file => import(pathToFileURL(path.join(root, 'examples/play/chase', file)));
const { resolveChaseScenario } = await load('simulation/scenario.mjs');
const { createChaseSimulationState, stepChaseSimulation } = await load('simulation/simulation.mjs');
const { getChaseScenarioDefinition } = await load('scenarios/index.mjs');
const config = JSON.parse(fs.readFileSync(path.join(here, '../maps/chaser-depth-obstacles.json')));
const cases = [];
for (const [name, x, z, angle, actions] of [
  ['straight-contact-reverse', 0, 3.05, -Math.PI/2, [[100, true, false, 0], [30, false, true, 0]]],
  ['steering', 0, 3.05, -Math.PI/2, [[80, true, false, .35], [40, false, true, -.65], [10, false, false, 1]]],
  ['boundary-contact', 3.8, 3, 0, [[80, true, false, 0], [20, false, true, .7]]],
  ['rotated-obstacle', -.75, 2.8, -Math.PI/2, [[80, true, false, .08], [30, false, true, 0]]],
]) {
  const definition = structuredClone(getChaseScenarioDefinition('chaser-depth-obstacles'));
  definition.runtime.chaserControlSource = 'ws';
  definition.runtime.programmaticChaserEnabled = false;
  definition.actors.chaser.position = {x,z};
  definition.actors.chaser.direction = {x:Math.cos(angle),z:Math.sin(angle)};
  definition.vehicleSettings.chaserSpeedUnitsPerFrame = config.vehicle.speedUnitsPerFrame;
  definition.vehicleSettings.maxSteeringAngleRadians = config.vehicle.maxSteeringAngleRadians;
  const state = createChaseSimulationState({scenario:resolveChaseScenario(definition)});
  const frames = [];
  for (const [count,forward,reverse,steering] of actions) {
    for (let index=0;index<count;index++) {
      const input = {source:'ws',forward,reverse,steering};
      stepChaseSimulation(state,{humanInput:input});
      frames.push({input,position:{...state.chaserPosition},direction:{...state.chaserLookDirection}});
    }
  }
  cases.push({name,spawn:{x,z,headingRadians:angle},frames});
}
const formatted = cases.map(({name,spawn,frames}) => `    {"name":${JSON.stringify(name)},"spawn":${JSON.stringify(spawn)},"frames":[\n${frames.map(frame=>'      '+JSON.stringify(frame)).join(',\n')}\n    ]}`).join(',\n');
fs.writeFileSync(path.join(here,'reference.json'),`{\n  "source":"GeorgeLuo/metrics-ui",\n  "commit":${JSON.stringify(commit)},\n  "cases":[\n${formatted}\n  ]\n}\n`);
