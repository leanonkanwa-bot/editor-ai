import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { authApi } from "../lib/api";

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setMessage("Lien de vérification invalide.");
      return;
    }
    authApi.verifyEmail(token)
      .then(r => {
        setState("success");
        setMessage(r.data.message || "Email vérifié avec succès !");
      })
      .catch(err => {
        setState("error");
        setMessage(err?.response?.data?.detail || "Lien invalide ou expiré.");
      });
  }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-10 max-w-md w-full text-center">
        <span className="text-4xl block mb-4">
          {state === "loading" ? "⏳" : state === "success" ? "✅" : "❌"}
        </span>
        <h1 className="font-heading text-xl font-bold text-white mb-2">
          {state === "loading" ? "Vérification…" : state === "success" ? "Email vérifié !" : "Lien invalide"}
        </h1>
        <p className="text-sm text-slate-400 mb-6">{state === "loading" ? "Un instant…" : message}</p>
        {state !== "loading" && (
          <Link to="/dashboard"
            className="inline-block px-6 py-2.5 bg-brand-500 hover:bg-brand-400 rounded-xl text-sm font-semibold transition-colors">
            Accéder au tableau de bord →
          </Link>
        )}
      </div>
    </div>
  );
}
