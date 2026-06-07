import {
  AlertTriangle,
  Brain,
  Cpu,
  FileText,
  RefreshCw,
  Search,
} from 'lucide-react';

function CrossProjectSearchCard({
  crossRole,
  setCrossRole,
  crossQuery,
  setCrossQuery,
  crossScope,
  setCrossScope,
  crossLimit,
  setCrossLimit,
  crossLoading,
  crossError,
  crossResults,
  handleCrossSearch,
}) {
  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden flex flex-col">
      <div className="px-6 py-4 bg-surface-sunken border-b-2 border-border-subtle flex justify-between items-center">
        <div className="flex items-center space-x-3">
          <Search className="h-5 w-5 text-status-info shrink-0" />
          <div>
            <span className="text-2xs font-bold text-text-muted uppercase tracking-wider">Search Engine</span>
            <h3 className="font-extrabold text-sm text-text-primary uppercase tracking-wide mt-0.5">Cross-Project Memory Retrieval</h3>
          </div>
        </div>
      </div>

      <form onSubmit={handleCrossSearch} className="p-6 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div>
            <label className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-1.5">
              Agent Role
            </label>
            <select
              value={crossRole}
              onChange={(event) => setCrossRole(event.target.value)}
              className="w-full rounded-xl border-2 border-border-subtle bg-surface-raised px-3 py-2.5 text-xs font-semibold text-text-primary focus:outline-none focus:ring-2 focus:ring-status-info/30"
            >
              <option value="implementation">Implementation</option>
              <option value="ui">UI</option>
              <option value="qa">QA</option>
            </select>
          </div>

          <div className="md:col-span-2">
            <label className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-1.5">
              Memory Query
            </label>
            <input
              type="text"
              value={crossQuery}
              onChange={(event) => setCrossQuery(event.target.value)}
              placeholder="Enter keywords or semantic query..."
              required
              className="w-full rounded-xl border-2 border-border-subtle bg-surface-raised px-3 py-2 text-xs font-semibold text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-status-info/30"
            />
          </div>

          <div>
            <button
              type="submit"
              disabled={crossLoading}
              className="w-full bg-accent hover:bg-accent disabled:bg-surface-sunken disabled:text-text-muted text-text-inverse border-2 border-accent-text px-4 py-2 rounded-xl text-xs font-extrabold uppercase tracking-wider shadow-button hover:shadow-button disabled:shadow-none hover:translate-y-0.5 active:translate-y-1 transition-all flex items-center justify-center gap-1.5 focus:outline-none cursor-pointer"
            >
              {crossLoading ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              <span>Search</span>
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pt-2 border-t border-dashed border-border-subtle">
          <div className="md:col-span-3">
            <label className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-1.5">
              Scope (comma-separated project keys, or 'all')
            </label>
            <input
              type="text"
              value={crossScope}
              onChange={(event) => setCrossScope(event.target.value)}
              placeholder="e.g. all, project-a, project-b"
              className="w-full rounded-xl border-2 border-border-subtle bg-surface-raised px-3 py-2 text-xs font-semibold text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-status-info/30"
            />
          </div>

          <div>
            <label className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-1.5">
              Max Results (k)
            </label>
            <input
              type="number"
              min="1"
              max="50"
              value={crossLimit}
              onChange={(event) => setCrossLimit(parseInt(event.target.value) || 5)}
              className="w-full rounded-xl border-2 border-border-subtle bg-surface-raised px-3 py-2 text-xs font-semibold text-text-primary focus:outline-none focus:ring-2 focus:ring-status-info/30"
            />
          </div>
        </div>
      </form>

      {(crossLoading || crossError || crossResults) && (
        <div className="border-t-2 border-border-subtle p-6 bg-surface-sunken space-y-4">
          <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">
            Cross-Project Search Results
          </h4>

          {crossLoading && (
            <div className="flex items-center justify-center p-8 text-text-secondary font-bold text-xs bg-surface-raised border border-border-subtle rounded-xl">
              <RefreshCw className="h-4 w-4 animate-spin mr-2 text-accent" />
              <span>Retrieving memories across projects...</span>
            </div>
          )}

          {crossError && (
            <div className="bg-status-warning-bg border-2 border-status-warning p-4 rounded-xl text-status-warning-text text-xs font-semibold flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <span>{crossError}</span>
            </div>
          )}

          {!crossLoading && !crossError && crossResults && crossResults.length === 0 && (
            <div className="text-center py-8 bg-surface-raised border border-border-subtle rounded-xl text-text-muted italic text-xs font-semibold">
              no cross-project matches
            </div>
          )}

          {!crossLoading && !crossError && crossResults && crossResults.length > 0 && (
            <div className="space-y-3">
              {crossResults.map((item) => (
                <div
                  key={item.id}
                  className="w-full text-left border border-border-subtle p-4 rounded-xl space-y-2 bg-surface-raised shadow-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-2xs font-bold text-accent-text">
                      {item.id}
                    </span>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="bg-status-info-bg text-status-info-text px-2 py-0.5 rounded uppercase text-2xs font-extrabold border border-status-info/30">
                        {item.horizon || 'event'}
                      </span>
                      {item.score !== undefined && item.score !== null && (
                        <span className="bg-status-warning-bg text-status-warning-text px-2 py-0.5 rounded border border-status-warning/30 text-2xs font-extrabold uppercase">
                          Score: {typeof item.score === 'number' ? item.score.toFixed(2) : item.score}
                        </span>
                      )}
                      <span className="bg-surface-sunken text-text-secondary px-2 py-0.5 rounded uppercase text-2xs font-extrabold border border-border-subtle">
                        Project: {item.project_key}
                      </span>
                    </span>
                  </div>
                  <p className="text-xs font-semibold leading-relaxed text-text-primary whitespace-pre-wrap">
                    {item.text || item.body || 'No event body recorded.'}
                  </p>
                  <div className="flex flex-wrap items-center gap-2 text-2xs font-bold text-text-muted">
                    <span className="font-mono text-text-muted">{item.timestamp || item.ts}</span>
                    {item.source && (
                      <span className="font-mono text-text-muted truncate max-w-full">{item.source}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MemoryRoleCard({
  role,
  eventBrowser,
  handleToggleMemoryEvents,
  handleMemoryEventSearchChange,
  handleOpenMemoryEvent,
}) {
  const browseEvents = Array.isArray(eventBrowser.events) ? eventBrowser.events : [];

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden flex flex-col">
      <div className="px-6 py-4 bg-surface-sunken border-b-2 border-border-subtle flex justify-between items-center">
        <div className="flex items-center space-x-3">
          <Cpu className="h-5 w-5 text-status-info shrink-0" />
          <div>
            <span className="text-2xs font-bold text-text-muted uppercase tracking-wider">Agent Role</span>
            <h3 className="font-extrabold text-sm text-text-primary uppercase tracking-wide mt-0.5">{role.role}</h3>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => handleToggleMemoryEvents(role.role)}
            className="bg-surface-raised hover:bg-status-info-bg text-text-secondary hover:text-status-info-text border-2 border-border-subtle px-3 py-1 rounded-xl text-2xs font-extrabold tracking-wider uppercase transition-all duo-button-physics focus:outline-none focus:ring-2 focus:ring-status-info/30 flex items-center gap-1.5"
            aria-expanded={Boolean(eventBrowser.open)}
          >
            <FileText className="h-3.5 w-3.5" />
            <span>Browse events</span>
          </button>
          <span className="bg-status-success-bg text-status-success-text border-2 border-status-success/30 px-3 py-1 rounded-xl text-2xs font-extrabold tracking-wider uppercase">
            {role.event_count || 0} events
          </span>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {role.event_count === 0 ? (
          <div className="text-center py-8 bg-surface-sunken border border-border-subtle rounded-xl text-text-muted italic text-xs font-semibold">
            no events yet
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border-2 border-border-subtle rounded-2xl p-4 bg-surface-sunken space-y-2">
                <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted flex items-center space-x-1">
                  <span>Long-Term Memory</span>
                </h4>
                <p className="text-xs text-text-secondary font-semibold leading-relaxed">
                  {role.summaries?.['long-term'] || 'No long-term summary recorded.'}
                </p>
              </div>

              <div className="border-2 border-border-subtle rounded-2xl p-4 bg-surface-sunken space-y-2">
                <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted flex items-center space-x-1">
                  <span>Mid-Term Memory</span>
                </h4>
                <p className="text-xs text-text-secondary font-semibold leading-relaxed">
                  {role.summaries?.['mid-term'] || 'No mid-term summary recorded.'}
                </p>
              </div>

              <div className="border-2 border-border-subtle rounded-2xl p-4 bg-surface-sunken space-y-2">
                <h4 className="text-2xs font-extrabold uppercase tracking-wider text-text-muted flex items-center space-x-1">
                  <span>Short-Term Memory</span>
                </h4>
                <p className="text-xs text-text-secondary font-semibold leading-relaxed whitespace-pre-wrap">
                  {role.short_term || 'No short-term text recorded.'}
                </p>
              </div>
            </div>

            {eventBrowser.open && (
              <div className="space-y-3 pt-2">
                <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 border-b border-border-subtle pb-2">
                  <h4 className="text-2xs font-bold uppercase tracking-wider text-text-muted flex items-center space-x-1.5">
                    <span>Browse Events</span>
                  </h4>
                  <label className="relative block w-full md:w-72">
                    <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" />
                    <input
                      type="search"
                      value={eventBrowser.query || ''}
                      onChange={(event) => handleMemoryEventSearchChange(role.role, event.target.value)}
                      placeholder="Search events"
                      className="w-full rounded-xl border-2 border-border-subtle bg-surface-raised py-2 pl-9 pr-3 text-xs font-semibold text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-status-info/30"
                    />
                  </label>
                </div>

                {eventBrowser.loading ? (
                  <div className="flex items-center justify-center p-8 text-text-secondary font-bold text-xs bg-surface-sunken border border-border-subtle rounded-xl">
                    <RefreshCw className="h-4 w-4 animate-spin mr-2 text-accent" />
                    <span>Loading events...</span>
                  </div>
                ) : eventBrowser.error ? (
                  <div className="bg-status-warning-bg border-2 border-status-warning p-4 rounded-xl text-status-warning-text text-xs font-semibold flex items-start gap-2">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>{eventBrowser.error}</span>
                  </div>
                ) : browseEvents.length === 0 ? (
                  <div className="text-center py-6 bg-surface-sunken border border-border-subtle rounded-xl text-text-muted italic text-2xs font-semibold">
                    No memory events match this search.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {browseEvents.map((event) => (
                      <button
                        type="button"
                        key={event.id}
                        onClick={() => handleOpenMemoryEvent(event.id)}
                        className="w-full text-left border border-border-subtle p-4 rounded-xl space-y-2 bg-surface-raised hover:bg-surface-sunken shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-mono text-2xs font-bold text-accent-text">{event.id}</span>
                          <span className="flex flex-wrap items-center gap-2">
                            {event.is_superseded && (
                              <span className="bg-surface-sunken text-text-muted px-2 py-0.5 rounded uppercase text-2xs font-extrabold border border-border-subtle">
                                Superseded
                              </span>
                            )}
                            <span className="bg-status-info-bg text-status-info-text px-2 py-0.5 rounded uppercase text-2xs font-extrabold border border-status-info/30">
                              {event.kind || event.horizon || 'event'}
                            </span>
                          </span>
                        </div>
                        <p className="text-xs font-semibold leading-relaxed text-text-primary whitespace-pre-wrap">
                          {event.body || 'No event body recorded.'}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 text-2xs font-bold text-text-muted">
                          <span className="font-mono text-text-muted">{event.timestamp || event.ts}</span>
                          {event.source && (
                            <span className="font-mono text-text-muted truncate max-w-full">{event.source}</span>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="space-y-3 pt-2">
              <div className="flex justify-between items-center border-b border-border-subtle pb-2">
                <h4 className="text-2xs font-bold uppercase tracking-wider text-text-muted flex items-center space-x-1.5">
                  <span>Retrieved Facts</span>
                </h4>
                {role.latest_retrieval?.job_id && (
                  <span className="text-2xs font-mono text-text-muted">
                    Last Job: <span className="font-bold text-text-secondary font-semibold">{role.latest_retrieval.job_id}</span>
                  </span>
                )}
              </div>

              {role.latest_retrieval?.relevant_events && role.latest_retrieval.relevant_events.length > 0 ? (
                <div className="space-y-3">
                  {role.latest_retrieval.relevant_events.map((event, idx) => (
                    <div key={event.id || idx} className="border border-border-subtle p-4 rounded-xl space-y-2.5 bg-surface-raised shadow-sm">
                      <p className="text-xs font-mono leading-relaxed text-text-primary bg-surface-sunken p-2.5 rounded-lg border border-dashed border-border-subtle whitespace-pre-wrap select-all select-text font-medium">
                        {event.text}
                      </p>
                      <div className="flex flex-wrap items-center justify-between gap-2 text-2xs font-bold text-text-muted">
                        <div className="flex items-center space-x-2">
                          <button
                            type="button"
                            onClick={() => handleOpenMemoryEvent(event.id)}
                            className="bg-status-success-bg hover:bg-status-success-bg text-status-success-text px-2 py-0.5 rounded font-mono border-2 border-status-success/30 shadow-button transition-all duo-button-physics focus:outline-none focus:ring-2 focus:ring-accent/40"
                            aria-label={`Open full memory event ${event.id}`}
                          >
                            {event.id}
                          </button>
                          <span className="bg-status-info-bg text-status-info-text px-2 py-0.5 rounded uppercase text-2xs border border-status-info/30">
                            {event.horizon}
                          </span>
                          {event.source && (
                            <span className="text-text-muted flex items-center space-x-1">
                              <span>Source:</span>
                              <span className="font-mono text-text-secondary truncate max-w-[200px]">{event.source}</span>
                            </span>
                          )}
                        </div>
                        {event.score !== undefined && event.score !== null && (
                          <span className="bg-status-warning-bg text-status-warning-text px-2 py-0.5 rounded border border-status-warning/30">
                            Score: {typeof event.score === 'number' ? event.score.toFixed(2) : event.score}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-6 bg-surface-sunken border border-border-subtle rounded-xl text-text-muted italic text-2xs font-semibold">
                  No relevant facts retrieved in the latest job.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function MemoryTab({
  state,
  loadingMemory,
  memoryError,
  memoryData,
  memoryEventBrowsers,
  crossRole,
  setCrossRole,
  crossQuery,
  setCrossQuery,
  crossScope,
  setCrossScope,
  crossLimit,
  setCrossLimit,
  crossLoading,
  crossError,
  crossResults,
  handleCrossSearch,
  handleToggleMemoryEvents,
  handleMemoryEventSearchChange,
  handleOpenMemoryEvent,
}) {
  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Compounding Memory Layer</h2>
        <p className="text-xs text-text-muted">Long-term agent memory horizons and the semantic facts retrieved for the latest worker dispatches.</p>
      </div>

      <CrossProjectSearchCard
        crossRole={crossRole}
        setCrossRole={setCrossRole}
        crossQuery={crossQuery}
        setCrossQuery={setCrossQuery}
        crossScope={crossScope}
        setCrossScope={setCrossScope}
        crossLimit={crossLimit}
        setCrossLimit={setCrossLimit}
        crossLoading={crossLoading}
        crossError={crossError}
        crossResults={crossResults}
        handleCrossSearch={handleCrossSearch}
      />

      {!state ? (
        <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-8 text-center space-y-4 max-w-md mx-auto">
          <Brain className="h-12 w-12 text-status-info mx-auto animate-pulse" />
          <h3 className="font-extrabold text-base text-text-primary">No Active Runs</h3>
          <p className="text-xs text-text-muted font-semibold">
            This project does not have an active run or initialized workspace. Please run a workflow first to seed and activate the memory layer.
          </p>
        </div>
      ) : loadingMemory ? (
        <div className="flex items-center justify-center p-12 text-text-secondary font-bold">
          <RefreshCw className="h-5 w-5 animate-spin mr-2 text-accent" />
          <span>Loading memory horizons...</span>
        </div>
      ) : memoryError ? (
        <div className="bg-status-warning-bg border-2 border-status-warning p-6 rounded-2xl text-center space-y-2 max-w-xl mx-auto">
          <AlertTriangle className="h-8 w-8 text-status-warning mx-auto" />
          <h3 className="font-extrabold text-sm text-status-warning-text">Memory Endpoint Not Available Yet</h3>
          <p className="text-xs text-status-warning-text font-semibold leading-relaxed">
            The backend memory query API might not be deployed or is temporarily offline. Details: {memoryError}
          </p>
        </div>
      ) : !memoryData || !memoryData.roles || memoryData.roles.length === 0 ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No memory data recorded yet for this project.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6">
          {memoryData.roles.map((role) => (
            <MemoryRoleCard
              key={role.role}
              role={role}
              eventBrowser={memoryEventBrowsers[role.role] || {}}
              handleToggleMemoryEvents={handleToggleMemoryEvents}
              handleMemoryEventSearchChange={handleMemoryEventSearchChange}
              handleOpenMemoryEvent={handleOpenMemoryEvent}
            />
          ))}
        </div>
      )}
    </div>
  );
}
