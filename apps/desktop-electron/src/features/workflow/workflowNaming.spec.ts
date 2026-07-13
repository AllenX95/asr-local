import { describe, expect, it } from 'vitest'
import { nextBaseNameForAudioSelection } from './workflowNaming.js'

describe('nextBaseNameForAudioSelection', () => {
  it('renames an automatically named draft when a new audio is selected', () => {
    expect(nextBaseNameForAudioSelection('260713-博云访谈', 'C:/recordings/260713-博云访谈.aac', 'C:/recordings/260713-南大潘老师访谈.m4a'))
      .toBe('260713-南大潘老师访谈')
  })

  it('keeps a manually edited task name when a new audio is selected', () => {
    expect(nextBaseNameForAudioSelection('潘老师访谈纪要', 'C:/recordings/260713-博云访谈.aac', 'C:/recordings/260713-南大潘老师访谈.m4a'))
      .toBe('潘老师访谈纪要')
  })
})
