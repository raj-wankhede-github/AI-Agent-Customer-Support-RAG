import type { ReactNode } from "react";
import { Link, Navigate, useLocation } from "react-router";
import { EmptyState, FullPageSpinner } from "../components/ui";
import type { UserRole } from "../lib/types";
import { homePathFor, useAuth } from "./AuthContext";

export function RequireAuth({ roles, children }: { roles?: UserRole[]; children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner label="Checking your session" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (roles && !roles.includes(user.role)) {
    return (
      <EmptyState
        icon="lock"
        title="You don't have access to this page"
        description="Your account's role doesn't include this area."
        action={<Link className="text-sm font-medium text-brand-700 hover:underline" to={homePathFor(user)}>Go to your home page</Link>}
      />
    );
  }
  return <>{children}</>;
}
