'use strict'

const { PostHog } = require('posthog-node')

const apiKey = process.env.POSTHOG_API_KEY
const host = process.env.POSTHOG_HOST

if (!apiKey) {
  if (process.env.NODE_ENV !== 'production') {
    throw new Error(
      'POSTHOG_API_KEY variable required by PostHog is missing or un-configured, ' +
      'this causes events to be silently missed. ' +
      'This error stops appearing once POSTHOG_API_KEY is configured'
    )
  }
}

if (!host) {
  if (process.env.NODE_ENV !== 'production') {
    throw new Error(
      'POSTHOG_HOST variable required by PostHog is missing or un-configured, ' +
      'this causes events to be silently missed. ' +
      'This error stops appearing once POSTHOG_HOST is configured'
    )
  }
}

const posthog = apiKey
  ? new PostHog(apiKey, {
      host: host || 'https://us.i.posthog.com',
      enableExceptionAutocapture: true,
    })
  : null

process.on('SIGINT', async () => {
  if (posthog) await posthog.shutdown()
  process.exit(0)
})

process.on('SIGTERM', async () => {
  if (posthog) await posthog.shutdown()
  process.exit(0)
})

module.exports = posthog
