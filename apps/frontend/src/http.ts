import ky, { isHTTPError } from 'ky';

export const backend = new URL(import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000').origin;

const sessionKey = `chess:${backend}`;
export let token = sessionStorage.getItem(sessionKey) ?? '';

export function setToken(value: string) {
  if (value) sessionStorage.setItem(sessionKey, value);
  else       sessionStorage.removeItem(sessionKey);
  token = value;
}


export const http = ky.create({
  baseUrl     : `${backend}/api/`,
  credentials : 'omit',
  timeout     : 10000,
  retry       : 0,

  hooks: {
    beforeRequest: [
      ({ request }) => {
        if (token) request.headers.set('Authorization', `Bearer ${token}`);
      },
    ],

    beforeError: [
      ({ error }) => {
        if (!isHTTPError<{ detail?: unknown }>(error)) return error;

        const body   = error.data;
        const detail = typeof body === 'object' ? body?.detail : undefined;

        error.message = typeof detail === 'string'
          ? detail
          : 'Unable to complete the request.';

        return error;
      },
    ],
  },
});
