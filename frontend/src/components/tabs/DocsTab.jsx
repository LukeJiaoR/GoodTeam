import { useState } from 'react';
import {
  BookOpen,
  Activity,
  FileCheck,
  Shield,
  Layers,
  Database,
  ArrowRight,
} from 'lucide-react';

export default function DocsTab({ projectRoot = '' }) {
  const [activeSection, setActiveSection] = useState('about');

  const sections = [
    { id: 'about', label: 'About GoodTeam', icon: BookOpen },
    { id: 'loop', label: 'Operator Loop', icon: Activity },
    { id: 'surfaces', label: 'Core Surfaces', icon: Layers },
    { id: 'audits', label: 'Audit Trails', icon: FileCheck },
    { id: 'boundaries', label: 'Sandbox Boundaries', icon: Shield },
  ];

  const auditFiles = [
    { name: 'Typed Control Plane Phase Closure', path: 'docs/audit/typed-control-plane-phase-closure.md' },
    { name: 'Production Control Plane Sprint Closure', path: 'docs/audit/production-control-plane-sprint-closure.md' },
    { name: 'Review Workbench MVP Final Acceptance', path: 'docs/audit/review-workbench-mvp-final-acceptance.md' },
    { name: 'Ledger Evidence Final Acceptance', path: 'docs/audit/ledger-evidence-final-acceptance.md' },
    { name: 'Sandbox V0 Final Acceptance', path: 'docs/audit/sandbox-v0-final-acceptance.md' },
    { name: 'GitHub Integration Loop Closure', path: 'docs/audit/github-integration-loop-closure.md' },
  ];

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Operational Documentation</h2>
        <p className="text-xs text-text-muted">
          Static documentation desk outlining GoodTeam's control plane architecture, agent boundaries, and local audit entries.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-start">
        {/* Left Sidebar Table of Contents */}
        <div className="lg:col-span-1 space-y-2">
          {sections.map((sec) => {
            const Icon = sec.icon;
            const isActive = activeSection === sec.id;
            return (
              <button
                key={sec.id}
                onClick={() => setActiveSection(sec.id)}
                className={`w-full h-11 flex items-center px-4 transition-all rounded-xl border-2 text-left cursor-pointer select-none ${
                  isActive
                    ? 'bg-accent-bg border-accent text-accent-text font-black'
                    : 'bg-surface-raised border-border-subtle hover:bg-surface-sunken hover:border-border-strong font-bold text-text-secondary'
                }`}
              >
                <Icon className={`h-4.5 w-4.5 shrink-0 mr-3 ${isActive ? 'text-accent-text' : 'text-text-muted'}`} />
                <span className="text-2xs uppercase tracking-wider">{sec.label}</span>
              </button>
            );
          })}
        </div>

        {/* Right Content Area */}
        <div className="lg:col-span-3 bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 lg:p-8 space-y-6 min-h-[480px]">
          {activeSection === 'about' && (
            <div className="space-y-4">
              <h3 className="text-sm font-black text-text-primary uppercase tracking-wider border-b border-border-subtle pb-2">
                About GoodTeam
              </h3>
              <p className="text-xs text-text-secondary leading-relaxed font-medium">
                GoodTeam is an autonomous control-plane and multi-agent workflow orchestration system designed to coordinate software development, validation, and code delivery. By adopting a local-first architecture, it ensures full data ownership and absolute portability, storing all execution records, audit trials, and compounding developer memories directly in your local directory.
              </p>
              <div className="p-4 bg-surface-sunken border border-border-subtle rounded-xl flex items-start space-x-3 text-2xs text-text-secondary leading-relaxed font-semibold">
                <Database className="h-5 w-5 text-accent shrink-0 mt-0.5" />
                <div>
                  <h4 className="font-extrabold text-text-primary mb-1">Local-First Ledger</h4>
                  Every transition, file diff, review decision, and preflight check is recorded as an immutable log inside your project workspace's <code className="px-1.5 py-0.5 bg-surface-raised rounded font-mono text-3xs border border-border-subtle">.goodteam/</code> folder, ensuring your team has self-contained provenance.
                </div>
              </div>
            </div>
          )}

          {activeSection === 'loop' && (
            <div className="space-y-4">
              <h3 className="text-sm font-black text-text-primary uppercase tracking-wider border-b border-border-subtle pb-2">
                Operator Loop
              </h3>
              <p className="text-xs text-text-secondary leading-relaxed font-medium">
                GoodTeam enforces a rigorous, multi-agent checkpoint loop to progress an initial request into a verified and integrated branch:
              </p>
              <ol className="relative border-l-2 border-border-subtle ml-2 text-xs space-y-4 font-semibold text-text-secondary">
                <li className="pl-6 relative">
                  <div className="absolute w-3 h-3 bg-accent rounded-full -left-[7px] border border-surface-raised" />
                  <span className="font-black text-text-primary">1. Intake & Takeover:</span> Reads project context, maps existing configurations, and establishes project objectives.
                </li>
                <li className="pl-6 relative">
                  <div className="absolute w-3 h-3 bg-accent rounded-full -left-[7px] border border-surface-raised" />
                  <span className="font-black text-text-primary">2. PRD & RFC Generation:</span> Automatically compiles product requirement documents and request-for-comment technical designs.
                </li>
                <li className="pl-6 relative">
                  <div className="absolute w-3 h-3 bg-accent rounded-full -left-[7px] border border-surface-raised" />
                  <span className="font-black text-text-primary">3. Task Dispatch:</span> Allocates discrete work parcels to specialized worker nodes. Enforces preflights and sandboxes.
                </li>
                <li className="pl-6 relative">
                  <div className="absolute w-3 h-3 bg-accent rounded-full -left-[7px] border border-surface-raised" />
                  <span className="font-black text-text-primary">4. Quality Gate review:</span> Validates artifacts, runs automated test suites, and opens the Review Workbench for human operator audits.
                </li>
                <li className="pl-6 relative">
                  <div className="absolute w-3 h-3 bg-accent rounded-full -left-[7px] border border-surface-raised" />
                  <span className="font-black text-text-primary">5. Release & Merge:</span> Merges validated branches and updates the local compounding memory ledger.
                </li>
              </ol>
            </div>
          )}

          {activeSection === 'surfaces' && (
            <div className="space-y-4">
              <h3 className="text-sm font-black text-text-primary uppercase tracking-wider border-b border-border-subtle pb-2">
                Completed Control-Plane Surfaces
              </h3>
              <p className="text-xs text-text-secondary leading-relaxed font-medium">
                GoodTeam has finalized its core control-plane infrastructure. The following surfaces are fully implemented:
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">Task Contract</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Specifies execution bounds, focus files, and target artifact expectations.</p>
                </div>
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">Runtime Policy</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Validates tool permissions, sandbox isolation configs, and worker environment preflights.</p>
                </div>
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">GitHub/CI Broker Status</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Queries remote repo branches, mergeability, CI statuses, and reviews.</p>
                </div>
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">Execution Replay Graph</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Renders node transition trees and audits historical event sequences.</p>
                </div>
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">Evidence Manifest</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Aggregates structural metadata, code coverage indices, and compiler output files.</p>
                </div>
                <div className="border border-border-subtle p-3.5 rounded-xl space-y-1 bg-surface-sunken">
                  <h4 className="text-2xs font-extrabold uppercase tracking-wide text-text-primary">Review Workbench</h4>
                  <p className="text-3xs text-text-secondary leading-relaxed">Enables the operator to approve, request revisions, or reject transitions with verified feedback logs.</p>
                </div>
              </div>
            </div>
          )}

          {activeSection === 'audits' && (
            <div className="space-y-4">
              <h3 className="text-sm font-black text-text-primary uppercase tracking-wider border-b border-border-subtle pb-2">
                Stable Docs & Audit Entry Points
              </h3>
              <p className="text-xs text-text-secondary leading-relaxed font-medium">
                The following documents represent verified audit trails and sprint closures. Click any item to directly open the local file in your environment:
              </p>
              <div className="space-y-2">
                {auditFiles.map((file) => {
                  const absoluteUrl = projectRoot
                    ? `file://${projectRoot.replace(/\\/g, '/')}/${file.path}`
                    : null;
                  return (
                    <div
                      key={file.path}
                      className="flex items-center justify-between p-3.5 bg-surface-sunken border border-border-subtle rounded-xl text-xs font-semibold"
                    >
                      <span className="text-text-primary truncate mr-4">{file.name}</span>
                      {absoluteUrl ? (
                        <a
                          href={absoluteUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent hover:underline flex items-center space-x-1 shrink-0 font-extrabold text-2xs uppercase tracking-wider"
                        >
                          <span>Open File</span>
                          <ArrowRight className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-text-muted text-3xs italic font-mono shrink-0">Path unknown</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {activeSection === 'boundaries' && (
            <div className="space-y-4">
              <h3 className="text-sm font-black text-text-primary uppercase tracking-wider border-b border-border-subtle pb-2">
                Operational Sandbox Boundaries
              </h3>
              <div className="text-xs leading-relaxed space-y-4 font-semibold text-text-secondary">
                <p>
                  GoodTeam separates the **Control Plane** (state machine, audit logging, evidence aggregation) from the **Model Reasoning Plane** (LLM operations, raw file editing, terminal execution).
                </p>
                <div className="border border-border-subtle rounded-2xl overflow-hidden">
                  <table className="w-full text-left border-collapse text-2xs font-semibold">
                    <thead>
                      <tr className="bg-surface-sunken border-b border-border-subtle text-text-muted uppercase tracking-wider">
                        <th className="p-3">Domain</th>
                        <th className="p-3">GoodTeam Control</th>
                        <th className="p-3">Model Workspace / Sandbox</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle bg-surface-base text-text-secondary">
                      <tr>
                        <td className="p-3 font-extrabold text-text-primary">Lifecycle</td>
                        <td className="p-3">Configures gates, records ledgers, manages reviews</td>
                        <td className="p-3">Noop (reacts to dispatches)</td>
                      </tr>
                      <tr>
                        <td className="p-3 font-extrabold text-text-primary">File Edits</td>
                        <td className="p-3">Reads project structure and creates checkpoints</td>
                        <td className="p-3">Executes diffs, writes patches, refines code</td>
                      </tr>
                      <tr>
                        <td className="p-3 font-extrabold text-text-primary">CLI Commands</td>
                        <td className="p-3">Runs staging verification and preflight commands</td>
                        <td className="p-3">Invokes build steps, compiler logs, and test binaries</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-2xs text-text-muted">
                  Note: Real worker invocation remains strictly explicit opt-in to maintain operator guardrails.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
