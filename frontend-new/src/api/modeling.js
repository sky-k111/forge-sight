import { governedModels, modelCatalog, modelingTasks } from "../mocks/data";
import { respond } from "./_client";

export async function listAlgorithms() {
  return respond(modelCatalog);
}

export async function listModelingTasks() {
  return respond(modelingTasks);
}

export async function listGovernedModels() {
  return respond(governedModels);
}
