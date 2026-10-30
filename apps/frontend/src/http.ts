import ky, { isHTTPError } from 'ky';

export const http = ky.create({
  baseUrl : '/api/',
  timeout : 10000,
  retry   : 0,

  hooks: {
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
