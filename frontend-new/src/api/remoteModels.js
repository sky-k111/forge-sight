import { httpGet, httpPost, httpRequest } from "./_http";

const base = "/api/v1/modeling/remote-models";
export const listRemoteModels = (sourceId) => httpGet(`${base}?sourceId=${sourceId}`);
export const discoverRemoteTools = (config) => httpPost(`${base}/discover`, config);
export const saveRemoteModel = (config, id) => id
  ? httpRequest(`${base}/${id}`, { method: "PUT", body: JSON.stringify(config) })
  : httpPost(base, config);
export const remoteModelAction = (id, action, records) => httpPost(`${base}/${id}/${action}`, { records });
export const listRemoteCalls = (id) => httpGet(`${base}/${id}/calls`);
