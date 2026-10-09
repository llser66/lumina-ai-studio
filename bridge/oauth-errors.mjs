// Error matching stays in server memory. Never expose a vendor response body,
// authorization code, callback URL, or bearer token through error diagnostics.
export function classifyOAuthFailure(error, context = {}) {
  const message = typeof error?.message === 'string' ? error.message : '';
  const stage = context.stage || 'starting';
  const status = Number.isInteger(context.http_status) ? context.http_status : undefined;
  let code = 'oauth_failed';
  if (/state mismatch/i.test(message)) code = 'oauth_state_mismatch';
  else if (/did not include.*chatgpt\.tokens\.use\.direct/i.test(message)) code = 'oauth_scope_missing';
  else if (/unsupported_country_region_territory/i.test(message)) code = 'oauth_region_unsupported';
  else if (/EADDRINUSE|Cannot bind.*1455|Port 1455 is in use|address already in use/i.test(message)) code = 'oauth_callback_busy';
  else if (/missing.*(?:authorization code|oauth state|client id)|callback URL must|issued client ID/i.test(message)) code = 'oauth_callback_invalid';
  else if (context.aborted || error?.name === 'AbortError' || /timed out|timeout/i.test(message)) code = 'oauth_timeout';
  else if (status >= 400 && stage === 'exchanging_token') code = 'oauth_token_rejected';
  else if (context.network_failed) code = 'oauth_network_error';
  else if (/invalid (?:access_token|refresh_token|expires_in|scope)/i.test(message)) code = 'oauth_token_invalid';
  return { ok: false, code, stage, ...(status ? { http_status: status } : {}), retryable: true };
}
