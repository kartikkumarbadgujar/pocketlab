import { useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { ChevronDown } from 'lucide-react'

const faqs = [
  ['What is PocketLab?', 'PocketLab tests Android applications under constrained device and network conditions, with a focus on the realities many users experience on entry-level phones.'],
  ['How does application testing work?', 'Create a project, register a device, choose a condition profile and scenario, then queue a run for an isolated Android runner. The runner reports measurements and findings back to PocketLab.'],
  ['What is network packet loss?', 'Packet loss is the share of transmitted network packets that do not arrive. A profile can request a loss condition; only a network measurement can report the observed loss.'],
  ['What is thermal throttling?', 'Android may reduce CPU or GPU performance when thermal conditions rise. PocketLab records Android thermal status separately from battery, CPU, and skin temperatures.'],
  ['What does low storage testing mean?', 'It checks how an app behaves when the device has little free space, including downloads, writes, cache growth, and recovery from storage errors.'],
  ['How are actual phone measurements collected?', 'A connected Android runner collects supported values from Android APIs, filesystem diagnostics, and network probes. Each sample records its source, timestamp, device, unit, and validity.'],
  ['What is the difference between configured and measured conditions?', 'Configured conditions describe what a test asks the runner to apply. Measured conditions describe what the device or network actually reported. A requested value is never treated as an observation.'],
  ['How does user-impact estimation work?', 'The estimate is monthly active users × condition population share × observed failure rate. PocketLab includes a Wilson confidence interval and requires condition groups to be declared mutually exclusive.'],
  ['Why does my phone show an unsupported thermal sensor?', 'Android devices expose different thermal APIs and sensors. If a sensor is unavailable, PocketLab records it as unsupported or unavailable instead of substituting another temperature.'],
  ['How can I reproduce a detected failure?', 'Open the finding and use its linked run, profile, scenario, reproduction steps, and evidence. Re-run the same versioned profile and scenario on a compatible device.'],
] as const

export default function FaqAccordion() {
  const [open, setOpen] = useState<number | null>(0)
  const reduce = useReducedMotion()
  return <div className="faq-list">{faqs.map(([question, answer], index) => {
    const expanded = open === index
    const contentId = `faq-content-${index}`
    return <article className={`faq-item ${expanded ? 'open' : ''}`} key={question}>
      <h2><button type="button" className="faq-trigger" aria-expanded={expanded} aria-controls={contentId} onClick={() => setOpen(expanded ? null : index)}><span>{question}</span><ChevronDown size={18} className="faq-chevron" aria-hidden="true" /></button></h2>
      <AnimatePresence initial={false}><motion.div id={contentId} className="faq-content" initial={false} animate={{ height: expanded ? 'auto' : 0, opacity: expanded ? 1 : 0 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: reduce ? 0 : 0.22 }} aria-hidden={!expanded}><p>{answer}</p></motion.div></AnimatePresence>
    </article>
  })}</div>
}
