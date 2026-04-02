import { LanguageProvider } from '@/lib/context/language-context';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <LanguageProvider>{children}</LanguageProvider>;
}
