import { describe, expect, it } from 'vitest'
import { taskControlActions } from './taskControls'

describe('taskControlActions', () => {
  it.each([
    ['queued', ['cancel']],
    ['running', ['pause', 'cancel']],
    ['paused', ['resume', 'cancel']],
    ['waiting_for_secret', ['cancel']],
    ['completed', []],
  ] as const)('maps %s workflows to supported expanded-task actions', (status, expected) => {
    expect(taskControlActions(status)).toEqual(expected)
  })
})
