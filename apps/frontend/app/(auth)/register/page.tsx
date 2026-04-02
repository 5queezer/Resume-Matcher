import { RegisterForm } from '@/components/auth/register-form';

export default function RegisterPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">Create Account</h1>
        <RegisterForm />
      </div>
    </div>
  );
}
