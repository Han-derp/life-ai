export async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
        ...options,
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...(options.headers || {}),
        },
    });

    if (!response.ok) {
        let detail = `请求失败（${response.status}）`;
        try {
            const body = await response.json();
            detail = body.detail || detail;
        } catch (_) {
            // Keep the default message.
        }
        throw new Error(detail);
    }

    if (response.status === 204) return null;
    return response.json();
}

export const get = (path) => api(path);
export const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
export const patch = (path, body) => api(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body) });
export const remove = (path) => api(path, { method: "DELETE" });
