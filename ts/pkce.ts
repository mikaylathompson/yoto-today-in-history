// Minimal PKCE helper for local tooling or future web integration
import crypto from 'node:crypto'

function base64UrlEncode(buf: Buffer) {
  return buf
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

export function generateVerifier(length = 64) {
  return base64UrlEncode(crypto.randomBytes(length))
}

export function challengeFromVerifier(verifier: string) {
  const hash = crypto.createHash('sha256').update(verifier).digest()
  return base64UrlEncode(hash)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const verifier = generateVerifier()
  const challenge = challengeFromVerifier(verifier)
  console.log(JSON.stringify({ verifier, challenge }, null, 2))
}
