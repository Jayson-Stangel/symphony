#!/usr/bin/env node
'use strict'

// An image-owned default for the supplied tool, resolved after its own parser.
// Never export a modern package-config default over explicit legacy settings,
// or use npm's deprecated arbitrary-config channel for an image default.
const fs = require('node:fs')
const gyp = require('../lib/node-gyp')()
gyp.parseArgv(process.argv)
const sources = ['nodedir', 'tarball', 'dist-url', 'disturl', 'devdir', 'target',
  'node_engine', 'node-engine']
const builds = gyp.todo.filter(command => ['configure', 'rebuild'].includes(command.name))
const release = require('../lib/process-release')
if (builds.length && !sources.some(key => Object.hasOwn(gyp.opts, key)) &&
    !process.env.NODEJS_ORG_MIRROR && process.execPath === '/usr/local/bin/node' &&
    builds.every(command => release(command.args, gyp, process.version, process.release).version === process.versions.node)) {
  const header = fs.readFileSync('/usr/local/include/node/node_version.h', 'utf8')
  const version = ['MAJOR', 'MINOR', 'PATCH'].map(key =>
    header.match(new RegExp('#define NODE_' + key + '_VERSION (\\d+)'))?.[1]).join('.')
  if (version === process.versions.node) {
    process.argv.splice(2, 0, '--nodedir=/usr/local')
  }
}
require('./node-gyp-upstream.js')
