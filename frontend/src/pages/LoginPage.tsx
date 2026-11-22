import { useState } from 'react'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Eye, EyeOff, ShieldCheck, Smartphone, Wifi } from 'lucide-react'
import DitherField from '../components/DitherField'
import KineticTextLoader from '../components/KineticTextLoader'
import { useAuth } from '../auth/useAuth'
import { errorMessage } from '../services/api'

const schema = z.object({ email: z.email('Enter a valid email address'), password: z.string().min(1, 'Enter your password') })
type FormValues = z.infer<typeof schema>
export default function LoginPage() {
  const [registerMode, setRegisterMode] = useState(false), [visible, setVisible] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const { login, register } = useAuth(), navigate = useNavigate()
  const { register: field, handleSubmit, formState: { errors } } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { email: '', password: '' } })
  const submit = handleSubmit(async ({ email, password }) => {
    setBusy(true); setError('')
    try { if (registerMode) { if (password.length < 12) { setError('Use at least 12 characters for a new password.'); return } await register(email, password) } else await login(email, password); navigate('/', { replace: true }) }
    catch (e) { setError(errorMessage(e, registerMode ? 'Could not create your account.' : 'Could not sign in.')) }
    finally { setBusy(false) }
  })
  return <main className="login-page"><section className="login-visual"><DitherField /><div className="login-visual-inner"><div className="login-brand"><div className="brand-mark"><span /></div><span>POCKETLAB</span></div><div className="login-copy"><div className="eyebrow light">ANDROID RELIABILITY LAB</div><h1>Test beyond<br />the flagship.</h1><p>Find the failures that only show up on real phones, under real constraints.</p><div className="login-feature-list"><div><Smartphone size={17} /><span>Entry-level device conditions</span></div><div><Wifi size={17} /><span>India-ready network profiles</span></div><div><ShieldCheck size={17} /><span>Measurements you can trust</span></div></div></div><div className="login-footnote"><span className="login-dash" />Android reliability under real-world Indian mobile conditions</div></div></section>
    <section className="login-panel"><div className="login-form-wrap"><div className="mobile-login-brand"><div className="brand-mark"><span /></div><span>POCKETLAB</span></div><div className="login-intro"><div className="welcome-glyph"><Smartphone size={23} /></div><div className="eyebrow">DEVELOPER WORKSPACE</div><h2>{registerMode ? 'Create your account' : 'Welcome back'}</h2><p>{registerMode ? 'Start testing Android apps beyond flagship hardware.' : 'Sign in to your testing workspace.'}</p></div>
      <form className="auth-form" onSubmit={submit} noValidate><label className="field-label" htmlFor="email">Work email</label><input id="email" type="email" autoComplete="email" placeholder="you@company.com" aria-invalid={Boolean(errors.email)} {...field('email')} />{errors.email && <span className="field-error">{errors.email.message}</span>}
        <div className="password-label-row"><label className="field-label" htmlFor="password">Password</label>{!registerMode && <span className="subtle-link">Use your account password</span>}</div><div className="password-wrap"><input id="password" type={visible ? 'text' : 'password'} autoComplete={registerMode ? 'new-password' : 'current-password'} placeholder={registerMode ? 'At least 12 characters' : 'Enter your password'} aria-invalid={Boolean(errors.password)} {...field('password')} /><button type="button" className="password-toggle" onClick={() => setVisible(!visible)} aria-label={visible ? 'Hide password' : 'Show password'}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>{errors.password && <span className="field-error">{errors.password.message}</span>}
        {error && <div className="form-error" role="alert">{error}</div>}<button type="submit" className="button button-primary auth-submit" disabled={busy}>{busy ? <KineticTextLoader message={registerMode ? 'Creating account...' : 'Signing in...'} compact /> : <>{registerMode ? 'Create account' : 'Sign in'} <ArrowRight size={16} /></>}</button>
      </form><p className="auth-switch">{registerMode ? 'Already have an account?' : 'New to PocketLab?'} <button type="button" onClick={() => { setRegisterMode(!registerMode); setError('') }}>{registerMode ? 'Sign in' : 'Create an account'}</button></p><p className="auth-legal">By continuing, you agree to use PocketLab only with applications and devices youâ€™re authorized to test.</p>
    </div></section></main>
}

