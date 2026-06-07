import { ExternalLink, FileText } from 'lucide-react';

export default function ArtifactsTab({ artifacts, handleEditArtifact, FilePath }) {
  const artifactEntries = artifacts ? Object.entries(artifacts) : [];

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">State Artifacts</h2>
        <p className="text-xs text-text-muted">Durable ledger file records generated and updated by this run.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {artifactEntries.map(([name, path]) => (
          <div
            key={name}
            className="bg-surface-raised border-2 border-border-subtle shadow-card p-4 rounded-2xl flex items-center justify-between hover:-translate-y-0.5 hover:shadow-card transition-all duration-200"
          >
            <div className="flex items-center space-x-3 overflow-hidden">
              <FileText className="h-6 w-6 text-status-info shrink-0" />
              <div className="overflow-hidden">
                <span className="font-extrabold text-2xs uppercase tracking-widest text-text-muted block mb-0.5">{name}</span>
                <FilePath path={path} segments={1} />
              </div>
            </div>
            <button
              type="button"
              onClick={() => handleEditArtifact(name)}
              className="bg-surface-raised hover:bg-status-info-bg border-2 border-border-subtle hover:border-status-info p-1.5 rounded-xl transition-all duo-button-physics shrink-0 text-text-muted hover:text-status-info-text"
              title="Open artifact"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        {artifactEntries.length === 0 && (
          <div className="col-span-full border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
            No artifacts recorded in this state yet.
          </div>
        )}
      </div>
    </div>
  );
}
