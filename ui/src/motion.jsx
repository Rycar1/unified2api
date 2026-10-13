import React, { useEffect, useRef, useState } from 'react'

export const reducedMotion = () =>
  document.documentElement.dataset.motion === 'reduced' || window.matchMedia('(prefers-reduced-motion: reduce)').matches
const easing = 'cubic-bezier(.16,1,.3,1)'

export function useMedia(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query),
      changed = () => setMatches(media.matches)
    changed()
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [query])
  return matches
}

export function DrawerFocus({ node, onClose }) {
  useDialogFocus(node, onClose)
  return null
}

function flatten(children, prefix = '') {
  return React.Children.toArray(children).flatMap((child, index) => {
    if (!React.isValidElement(child)) return []
    const key = `${prefix}${child.key ?? index}`
    return child.type === React.Fragment
      ? flatten(child.props.children, `${key}/`)
      : [React.cloneElement(child, { key, 'data-motion-key': key })]
  })
}

export class MotionRows extends React.Component {
  node = React.createRef()
  animations = []
  ghosts = []
  getSnapshotBeforeUpdate() {
    const next = new Set(flatten(this.props.children).map((child) => child.props['data-motion-key']))
    return [...(this.node.current?.children || [])].map((node) => ({
      key: node.dataset.motionKey,
      rect: node.getBoundingClientRect(),
      clone: next.has(node.dataset.motionKey) ? null : node.cloneNode(true),
    }))
  }
  componentDidMount() {
    this.animate([])
  }
  componentDidUpdate(_props, _state, snapshot) {
    this.animate(snapshot)
  }
  componentWillUnmount() {
    this.cancel()
  }
  cancel() {
    this.animations.forEach((animation) => animation.cancel())
    this.ghosts.forEach((node) => node.remove())
    this.animations = []
    this.ghosts = []
  }
  animate(snapshot) {
    this.cancel()
    if (reducedMotion() || !this.node.current || !Element.prototype.animate) return
    const previous = new Map(snapshot.map((row) => [row.key, row]))
    const current = new Set()
    ;[...this.node.current.children].forEach((node, index) => {
      const key = node.dataset.motionKey
      current.add(key)
      const rect = node.getBoundingClientRect(),
        old = previous.get(key)
      const delta = old ? old.rect.top - rect.top : 0
      if (!old || Math.abs(delta) > 0.5) {
        this.animations.push(
          node.animate(
            old
              ? [{ transform: `translateY(${delta}px)` }, { transform: 'translateY(0)' }]
              : [
                  { opacity: 0, transform: 'translateY(9px)' },
                  { opacity: 1, transform: 'translateY(0)' },
                ],
            { duration: Math.min(360, 220 + Math.abs(delta) * 0.3), easing, delay: old ? 0 : Math.min(index * 9, 72) },
          ),
        )
      }
    })
    snapshot
      .filter((row) => !current.has(row.key) && row.clone)
      .forEach((row) => {
        const node = row.clone
        node.setAttribute('aria-hidden', 'true')
        node.inert = true
        node.removeAttribute('id')
        node.querySelectorAll('[id]').forEach((item) => item.removeAttribute('id'))
        Object.assign(node.style, {
          position: 'fixed',
          top: `${row.rect.top}px`,
          left: `${row.rect.left}px`,
          width: `${row.rect.width}px`,
          height: `${row.rect.height}px`,
          display: 'table',
          tableLayout: 'fixed',
          pointerEvents: 'none',
          zIndex: 5,
        })
        document.body.append(node)
        this.ghosts.push(node)
        const animation = node.animate(
          [
            { opacity: 0.7, transform: 'translateY(0)' },
            { opacity: 0, transform: 'translateY(-5px)' },
          ],
          { duration: 120, easing },
        )
        this.animations.push(animation)
        animation.finished.then(() => node.remove()).catch(() => {})
      })
  }
  render() {
    return <tbody ref={this.node}>{flatten(this.props.children)}</tbody>
  }
}

export function PagePresence({ page, children }) {
  const node = useRef(null)
  useEffect(() => {
    if (reducedMotion() || !node.current?.animate) return undefined
    const animation = node.current.animate(
      [
        { opacity: 0, transform: 'translateY(10px)' },
        { opacity: 1, transform: 'translateY(0)' },
      ],
      { duration: 280, easing },
    )
    return () => animation.cancel()
  }, [page])
  return (
    <div className="page-content" ref={node}>
      {children}
    </div>
  )
}

export function useDialogFocus(node, close) {
  const callback = useRef(close)
  callback.current = close
  useEffect(() => {
    const previous = document.activeElement,
      overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const controls = () =>
      [
        ...(node.current?.querySelectorAll(
          'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]',
        ) || []),
      ].filter((element) => element.offsetParent !== null)
    controls()[0]?.focus()
    function keydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        callback.current()
      }
      if (event.key === 'Tab') {
        const items = controls(),
          first = items[0],
          last = items.at(-1)
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first?.focus()
        }
      }
    }
    window.addEventListener('keydown', keydown)
    return () => {
      window.removeEventListener('keydown', keydown)
      document.body.style.overflow = overflow
      previous?.focus?.()
    }
  }, [node])
}
