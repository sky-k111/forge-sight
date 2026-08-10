export async function respond(data) {
  return structuredClone(data);
}

export function notFound(message, code = "NOT_FOUND") {
  const error = new Error(message);
  error.code = code;
  return error;
}
