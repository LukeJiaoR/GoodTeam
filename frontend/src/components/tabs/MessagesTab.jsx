import MessageFeed from '../MessageFeed';

export default function MessagesTab({
  messageFeedProjectKey,
  messageFeedRunId,
  messageFeed,
  targetMessageFeedKey,
}) {
  return (
    <MessageFeed
      projectKey={messageFeedProjectKey}
      runId={messageFeedRunId}
      messages={messageFeed.key === targetMessageFeedKey ? messageFeed.messages : []}
      loading={messageFeed.key === targetMessageFeedKey && messageFeed.loading}
      error={messageFeed.key === targetMessageFeedKey ? messageFeed.error : ''}
    />
  );
}
