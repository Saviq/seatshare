let csrf = "";

export function setCsrf(value) {
  csrf = value;
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new Error("Unable to reach Seat Share. Check your connection and retry.");
  }
  if (!response.ok) {
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(`Server error (${response.status}). Please retry.`);
    }
    const data = await response.json();
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => `${item.field}: ${item.message}`).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
