import { transferableAbortController } from "node:util";

// Node's Request and jsdom's AbortController belong to different realms.
// Data-router requests must use the matching native signal constructor.
const controller = transferableAbortController();
globalThis.AbortController = controller.constructor;
globalThis.AbortSignal = controller.signal.constructor;
